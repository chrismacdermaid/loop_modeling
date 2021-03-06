#!/usr/bin/env python2

# The MIT License (MIT)
#
# Copyright (c) 2015 Shane O'Connor
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.

"""\
This script analyzes loop modeling prediction runs and produces the metrics for the benchmark. It has been written to
be generic and includes support for analyzing some standard Rosetta loop modeling methods (KIC, NGK).

Usage:
    analyze.py --output_directory <argument> --prefix <argument> [--data_extraction_method <argument> --expectn <argument> --topx <argument> --test_mode --dataset <argument>]

Options:

    -o --output_directory OUTPUT_DIR
        The path to the directory containing the prediction results.

    -p --prefix PREFIX
        A prefix used to name the output files.

    -m --data_extraction_method METHOD_NAME
        The function used to extract the data from the predictions. If you are adding your own method to the benchmark
        capture then you will need to implement a data extraction function. See "Method-specific functions" in this file.
        This defaults to the Rosetta KIC method. [default: KIC]

    -d --dataset DATASET
        The name of the dataset used for the benchmark run. Valid values are "12_res" and "14_17_res". [default: 12_res]

    --expectn EXPECTN
        The expected number of predicted structures expected per case. This is a useful check to make sure that all runs were successful
        and the analysis is using all expected data. [default: 500]

    --topx TOPX
        The number of best-scoring predictions to use to calculate the TopX metrics e.g. lowest RMSD among the top X-scoring
        models. [default: 5]

    --test_mode
        Used to test the analysis script. This setting limits expectn to 10, only considers 10 benchmark cases, and sets
        the test_mode variable in the data extraction method. Based on this variable, data extraction methods should limit
        the number of structures read per benchmark case to 10.

Authors:
    Shane O'Connor
"""

import sys
import os
import math
import glob
import json
import time
import pprint

from libraries import docopt
from libraries import install

import pandas
from rpy2.robjects.packages import importr
import rpy2.robjects as ro
import pandas.rpy.common as com

install.require_klab()
from klab import colortext
from klab.fs.fsio import read_file, get_file_lines, write_file
from klab.bio.pdb import PDB
from klab.bio.basics import backbone_atoms
from klab.pymath.cartesian.rmsd import compute_rmsd_by_matrix


class LoopPredictionSet(object):

    ### Constructors

    def __init__(self):
        self.loop_predictions = []
        self.loop_prediction_map= {}


    @staticmethod
    def from_list(loop_predictions):
        lps = LoopPredictionSet()
        for loop_prediction in loop_predictions:
            assert(loop_prediction.id not in lps.loop_predictions)
            lps.loop_predictions.append(loop_prediction)
            lps.loop_prediction_map[loop_prediction.id] = loop_prediction
        return lps


    ### Modifiers and mutators


    def add(self, id, score, pdb_id = None, rmsd = None, pdb_path = None, pdb_loop_residue_matrix = None):
        assert(id not in self.loop_predictions)
        lp = LoopPrediction(id, score, pdb_id = pdb_id, rmsd = rmsd, pdb_path = pdb_path, pdb_loop_residue_matrix = pdb_loop_residue_matrix)
        self.loop_predictions.append(lp)
        self.loop_prediction_map[id] = lp
        return lp


    def truncate(self, n):
        '''Keep the first n predictions according to the current order and discard the other predictions.'''
        lps_to_delete = self.loop_predictions[n:]
        for lp in lps_to_delete:
            del self.loop_prediction_map[lp.id]
        self.loop_predictions = self.loop_predictions[:n]
        assert(sorted([lp.id for lp in self.loop_predictions]) == sorted(self.loop_prediction_map.keys()))


    def get(self, id):
        return self.loop_prediction_map.get(id)


    ### Sorting functions


    def sort_by_rmsd(self):
        self.loop_predictions.sort(cmp = lambda a, b: LoopPrediction.sort_by_rmsd(a, b))


    def sort_by_score(self):
        self.loop_predictions.sort(cmp = lambda a, b: LoopPrediction.sort_by_score(a, b))


    ### Computational functions


    def compute_rmsds(self, reference_pdb_residue_matrix):
        for lp in self.loop_predictions:
            assert(not(lp.pdb_loop_residue_matrix is None))
            lp.rmsd = compute_rmsd_by_matrix(reference_pdb_residue_matrix, lp.pdb_loop_residue_matrix)


    def check_rmsds(self, reference_pdb_residue_matrix):
        for lp in self.loop_predictions:
            assert(not(lp.pdb_loop_residue_matrix is None) and (lp.rmsd != None))
            assert(lp.rmsd == compute_rmsd_by_matrix(reference_pdb_residue_matrix, lp.pdb_loop_residue_matrix))


    ### Informational functions


    def fraction_with_rmsd_lt(self, x, allow_failure = False, strict = True):
        c = 0
        for loop_prediction in self.loop_predictions:
            if loop_prediction.rmsd == None and not(allow_failure):
                raise Exception('Error: Some of the loop predictions are missing RMSD values.')
            if (strict and loop_prediction.rmsd < x) or (not(strict) and loop_prediction.rmsd <= x):
                c += 1
        return float(c) / float(len(self.loop_predictions))


    ### Standard method overrides


    def __getitem__(self, key):
        '''If key is a slice, return a new LoopPredictionSet. Otherwise, return the single loop prediction.'''
        if isinstance(key, slice):
            return LoopPredictionSet.from_list([self.loop_predictions[ii] for ii in xrange(*key.indices(len(self)))])
        elif isinstance(key, int):
            if key >= len(self.loop_predictions):
                raise IndexError('The set only contains {0} items and is zero-indexed but item #{1} was requested.'.format(len(self.loop_predictions), key))
            return self.loop_predictions[key]
        else:
            raise TypeError('Invalid argument type.')


    def __iter__(self):
        # Assume that the set was sorted as preferred before the call
        for lp in self.loop_predictions:
            yield lp


    def __len__(self):
        return len(self.loop_predictions)


    ### Standard method overrides


    def __repr__(self):
        s = []
        for lp in self.loop_predictions:
            s.append(str(lp))
        return '\n'.join(s)



class LoopPrediction(object):
    '''A generic class to store information about a loop prediction. To use this class, the loop modeling application
       should have some notion of rank/score i.e. that one prediction is 'better' than another and some unique form of
       identification e.g. an arbitrary integer or a filename.

       In practice, this class can be used to store: i) the id and score; ii) a path to a predicted structure; iii) the
       root mean square deviation from a reference structure;
    '''


    def __init__(self, id, score, pdb_id = None, rmsd = None, pdb_path = None, pdb_loop_residue_matrix = None):
        # pdb_loop_residue_matrix should be a pandas dataframe with X, Y, Z columns indexed by residue atom
        self.id = id
        self.score = score
        self.pdb_id = pdb_id
        self.rmsd = rmsd
        self.pdb_path = pdb_path
        self.pdb_loop_residue_matrix = pdb_loop_residue_matrix
        self._check_types()


    def _check_types(self):
        '''Basic type-checking.'''
        assert(isinstance(self.score, int) or isinstance(self.score, float))
        if self.rmsd:
            assert(isinstance(self.rmsd, int) or isinstance(self.rmsd, float))
        if self.pdb_path:
            assert(os.path.exists(self.pdb_path))


    ### Sorting functions
    #   Since we use more than one form of sorting, this seemed neater than implementing the __cmp__ function.


    @staticmethod
    def sort_by_score(a, b):
        assert(a.id != b.id)
        a._check_types()
        b._check_types()
        if a.score != b.score:
            if a.score < b.score: return -1
            else: return 1
        elif a.rmsd != b.rmsd:
            if a.rmsd < b.rmsd: return -1
            else: return 1
        else:
            if a.id < b.id: return -1
            else: return 1


    @staticmethod
    def sort_by_rmsd(a, b):
        assert(a.id != b.id)
        a._check_types()
        b._check_types()
        if a.rmsd != b.rmsd:
            if a.rmsd < b.rmsd: return -1
            else: return 1
        elif a.score != b.score:
            if a.score < b.score: return -1
            else: return 1
        else:
            if a.id < b.id: return -1
            else: return 1


    ### Standard method overrides


    def __repr__(self):
        self._check_types()
        s = ''
        if self.pdb_id:
            s += self.pdb_id + ': '
        s += '{0} '.format(str(self.id).ljust(10))
        s += 'Score: {0}.'.format(str(self.score).ljust(10))
        if self.rmsd:
            s += 'RMSD: {0}'.format(str(self.rmsd).ljust(10))
        if self.pdb_path:
            s += self.pdb_path
        return s


###
#  Method-specific functions
#
#  Each computational method should implement a function which accepts a output_directory and pdb_id parameter and returns
#  the results from a prediction run as a list of dicts with the keys id, score, predicted_structure, and pdb_loop_residue_matrix where:
#    - id is a unique identifier for the prediction e.g. an integer;
#    - score is a integer or float value assigning a score or rank to a prediction;
#    - predicted_structure is the path to a file for the predicted structure;
#    - pdb_loop_residue_matrix is a pandas dataframe containing the coordinates for the loop residue heavy atoms (N, CA, C, O).
#      This can be constructed using the PDB.extract_xyz_matrix_from_loop_json function.
#  The method should then be added to the data_extraction_methods dict.
#
#  A concrete example, get_kic_run_details, is given below for the Rosetta KIC methods contained in the repository.
###


def get_kic_run_details(output_directory, pdb_id, loop_sets, test_mode = False):
    '''This function returns the details required to set up the analysis for the Rosetta KIC and NGK methods.'''
    details = []
    c = 0
    for sc_file in glob.glob(os.path.join(output_directory, '{0}*.sc'.format(pdb_id))):

        # Determine the id
        sc_filename = os.path.split(sc_file)[1]
        assert(sc_filename.startswith('{0}_score'.format(pdb_id)))
        run_id = int(sc_filename[10:-3])

        # Determine the score
        sc_lines = [l.strip() for l in get_file_lines(sc_file) if l.strip()]
        assert(sc_lines[0] == 'SEQUENCE:')
        assert(sc_lines[1].split()[:2] == ['SCORE:', 'total_score'])
        assert(sc_lines[2].split()[0] == 'SCORE:')
        total_score = float(sc_lines[2].split()[1])

        # Determine the filepath of the predicted structure
        associated_pdb_file = os.path.join(output_directory, '{0}_{0}{1}_0001.pdb'.format(pdb_id, run_id))

        # Extract the PDB coordinates into a pandas dataframe (HDF5 format)
        assert(os.path.exists(associated_pdb_file))
        hdf5_file = os.path.splitext(associated_pdb_file)[0] + '.hdf5'
        if os.path.exists(hdf5_file):
            store = pandas.HDFStore(hdf5_file)
            pdb_loop_residue_matrix = store['dataframe']
            store.close()
        else:
            pdb_loop_residue_matrix = PDB.extract_xyz_matrix_from_loop_json(PDB.from_filepath(associated_pdb_file).structure_lines, loop_sets, atoms_of_interest = backbone_atoms, expected_num_residues = 12, expected_num_residue_atoms = 4)
            store = pandas.HDFStore(hdf5_file)
            store['dataframe'] = pdb_loop_residue_matrix
            store.close()

        details.append(dict(
            id = run_id,
            score = total_score,
            predicted_structure = associated_pdb_file,
            pdb_loop_residue_matrix = pdb_loop_residue_matrix,
        ))
        if test_mode:
            c += 1
            if c >= 10:
                break

    return details


data_extraction_methods = dict(
    KIC = get_kic_run_details,
)


###
#  Main function
###


def extract_analysis_data(dataset_list_file, output_directory, data_extraction_method, expectn, top_x, prefix, test_mode = False):
    '''This is the main function in this script and is where the basic analysis is compiled.

       output_directory should contain the results of the prediction run.
       data_extraction_method should be a function pointer to the method-specific function used to retrieve the prediction results e.g. get_kic_run_details
       expectn specifies how many predictions we expect to find (useful in case some jobs failed).
       top_x specifies how many of the best-scoring predictions should be used to generate the TopX metric results e.g.
       the Top5 RMSD metric value measures the lowest RMSD amongst the five best-scoring structures.
       prefix is used to name the output files.
    '''

    # Sanity check
    assert(top_x <= expectn)

    # Set up reference structures
    structures_folder = os.path.join('..', 'input', 'structures', '12_res')
    rcsb_references = os.path.join(structures_folder, 'rcsb', 'reference')
    rosetta_references = os.path.join(structures_folder, 'rosetta', 'reference')

    # Set up the per-case statistics dicts
    best_scoring_structures = {}
    median_scoring_structures = {}
    worst_scoring_structures = {}
    total_percent_subanstrom = {}
    top_x_percent_subanstrom = {}
    top_x_loop_prediction_sets = {}

    # Set up the input file used to generate the graph plotting the "percentage of subangstrom models" metric over
    # varying values of X used to select the TopX structures
    percentage_subangstrom_over_top_X_plot_input = ['PDB\tX\tPercentage of subangstrom cases for TopX']
    percent_subangrom_by_top_x = {}

    # Set up the summary analysis file
    csv_file = ['\t'.join(['PDB ID', 'Models', '%<1.0A', 'Top{0} %<1.0A'.format(top_x), 'Best score', 'Top{0} score'.format(top_x), 'Median score', 'Worst score', 'Closest score', 'Top1 RMSD', 'Top{0} RMSD'.format(top_x), 'Closest RMSD'])]

    # Read in the benchmark input
    pdb_ids = [os.path.splitext(os.path.split(s.strip())[1])[0] for s in get_file_lines(dataset_list_file) if s.strip()]

    # Truncate the benchmark input for test mode
    if test_mode:
        pdb_ids = pdb_ids[:10]

    # Analyze the performance for each case in the benchmark
    for pdb_id in pdb_ids:

        rcsb_reference_pdb = os.path.join(rcsb_references, pdb_id + '.pdb')
        assert(os.path.exists(rcsb_reference_pdb))
        rosetta_reference_pdb = os.path.join(rosetta_references, pdb_id + '.pdb')
        assert(os.path.exists(rosetta_reference_pdb))
        assert(len(pdb_id) == 4)
        loops_file = os.path.join(structures_folder, 'rosetta', 'pruned', '{0}.loop.json'.format(pdb_id))
        loop_sets = json.loads(read_file(loops_file))
        assert(len(loop_sets['LoopSet']) == 1)

        # Create a container for loop predictions
        loop_prediction_set = LoopPredictionSet()

        # Read the coordinates from the reference PDB file
        rcsb_reference_matrix = PDB.extract_xyz_matrix_from_loop_json(PDB.from_filepath(rcsb_reference_pdb).structure_lines, loop_sets, atoms_of_interest = backbone_atoms, expected_num_residues = 12, expected_num_residue_atoms = 4)
        rosetta_reference_matrix = PDB.extract_xyz_matrix_from_loop_json(PDB.from_filepath(rosetta_reference_pdb).structure_lines, loop_sets, atoms_of_interest = backbone_atoms, expected_num_residues = 12, expected_num_residue_atoms = 4)

        colortext.wgreen('\n\nReading in the run details for {0}:'.format(pdb_id))
        details = data_extraction_method(output_directory, pdb_id, loop_sets, test_mode = test_mode)
        for d in details:
            loop_prediction = loop_prediction_set.add(d['id'], d['score'], pdb_id = pdb_id, rmsd = None, pdb_path = d['predicted_structure'], pdb_loop_residue_matrix = d['pdb_loop_residue_matrix'])
        print(' Done')

        # Compute the RMSD for this case for the structure using the pandas dataframe
        # It is more efficient to do this after truncation if truncating by score but in the general case users will
        # probably want to consider all predictions. If not (e.g. for testing) then arbitrary subsets can be chosen
        # in the loop above
        colortext.wgreen('Computing RMSDs for {0}:'.format(pdb_id))
        loop_prediction_set.compute_rmsds(rcsb_reference_matrix)
        loop_prediction_set.check_rmsds(rosetta_reference_matrix)
        print(' Done\n')

        # Truncate the structures to the top expectn-scoring files
        loop_prediction_set.sort_by_score()
        loop_prediction_set.truncate(expectn)
        if len(loop_prediction_set) != expectn:
            print('Error: Expected {0} structures but only found {1}.'.format(expectn, len(loop_prediction_set)))
            sys.exit(1)

        # Create a new set containing the top-X-scoring structures and identify the median-scoring structure
        top_x_loop_prediction_sets[pdb_id] = loop_prediction_set[:top_x]
        median_scoring_structures[pdb_id] = loop_prediction_set[int(expectn / 2)]

        # Determine the lowest-/best-scoring structure
        best_scoring_structures[pdb_id] = loop_prediction_set[0]
        best_score = best_scoring_structures[pdb_id].score
        worst_scoring_structures[pdb_id] = loop_prediction_set[-1]
        worst_score = worst_scoring_structures[pdb_id].score
        assert(top_x_loop_prediction_sets[pdb_id][0] == best_scoring_structures[pdb_id])

        # Print structures
        colortext.warning('Top{0} structures'.format(top_x))
        print(top_x_loop_prediction_sets[pdb_id])
        colortext.warning('Top1 structure')
        print(best_scoring_structures[pdb_id])
        colortext.warning('Median (by score) structure')
        print(median_scoring_structures[pdb_id])
        colortext.warning('Lowest-scoring structures')
        print(worst_scoring_structures[pdb_id])

        # Create values for TopX variable plot
        loop_prediction_set.sort_by_score()
        for top_x_var in range(1, len(loop_prediction_set) + 1):
            new_subset = loop_prediction_set[:top_x_var]
            percent_subangstrom = 100 * new_subset.fraction_with_rmsd_lt(1.0)
            percentage_subangstrom_over_top_X_plot_input.append('{0}\t{1}\t{2}'.format(pdb_id, top_x_var, percent_subangstrom))
            percent_subangrom_by_top_x[top_x_var] = percent_subangrom_by_top_x.get(top_x_var, {})
            percent_subangrom_by_top_x[top_x_var][pdb_id] = percent_subangstrom

        total_percent_subanstrom[pdb_id] = 100 * loop_prediction_set.fraction_with_rmsd_lt(1.0)
        top_x_percent_subanstrom[pdb_id] = 100 * top_x_loop_prediction_sets[pdb_id].fraction_with_rmsd_lt(1.0)
        colortext.warning('Number of sub-angstrom cases in the full set of {0}: {1}'.format(expectn, total_percent_subanstrom[pdb_id]))
        colortext.warning('Number of sub-angstrom cases in the TopX structures: {1}'.format(expectn, top_x_percent_subanstrom[pdb_id]))

        loop_prediction_set.sort_by_rmsd()
        closest_rmsd = loop_prediction_set[0].rmsd
        closest_score = loop_prediction_set[0].score
        colortext.warning('RMSD of closest model: {0}'.format(closest_rmsd))
        colortext.warning('Score of closest model: {0}'.format(closest_score))

        top_1_rmsd = best_scoring_structures[pdb_id].rmsd

        top_x_rmsd = best_scoring_structures[pdb_id].rmsd
        top_x_score = best_scoring_structures[pdb_id].score
        for s in top_x_loop_prediction_sets[pdb_id]:
            if (s.rmsd < top_x_rmsd) or (s.rmsd == top_x_rmsd and s.score < top_x_score):
                top_x_rmsd = s.rmsd
                top_x_score = s.score
        assert(top_x_score <= worst_score)
        assert(top_x_rmsd <= top_1_rmsd)

        print('Top 1 RMSD (predicted vs Rosetta/RCSB reference structure): {0}'.format(top_1_rmsd))
        print('Top {0} RMSD (predicted vs Rosetta/RCSB reference structure): {1}'.format(top_x, top_x_rmsd))

        csv_file.append('\t'.join(map(str, [pdb_id, expectn, total_percent_subanstrom[pdb_id], top_x_percent_subanstrom[pdb_id], best_score, top_x_score, median_scoring_structures[pdb_id].score, worst_score, closest_score, top_1_rmsd, top_x_rmsd, closest_rmsd])))

    # Add a column of median percent subangstrom values
    for top_x_var, values_by_pdb in sorted(percent_subangrom_by_top_x.iteritems()):
        assert(sorted(values_by_pdb.keys()) == sorted(pdb_ids))
        median_value = sorted(values_by_pdb.values())[len(pdb_ids) / 2]
        percentage_subangstrom_over_top_X_plot_input.append('Median\t{1}\t{2}'.format(pdb_id, top_x_var, median_value))

    write_file('{0}analysis.csv'.format(prefix), '\n'.join(csv_file))
    write_file('{0}analysis.tsv'.format(prefix), '\n'.join(csv_file))
    write_file('{0}percentage_subangstrom_over_top_X.tsv'.format(prefix), '\n'.join(percentage_subangstrom_over_top_X_plot_input))


# todo: invoke R
r_script = '''
library(ggplot2)

subangstrom_data <- read.table('top_expectn.csv', header = TRUE, sep = '\t', col.names=c('PDB', 'X', 'Percentage'));

a <- ggplot(data=subset(subangstrom_data, X <= 100), aes(x=X, Percentage, color=PDB)) +
   geom_vline(xintercept = 3, color="red", alpha = 0.5, linetype = "longdash") +
   geom_vline(xintercept = 5, color="green", alpha = 0.5, linetype = "longdash") +
   geom_vline(xintercept = 10,color="blue", alpha = 0.5, linetype = "longdash") +
   geom_smooth(method = "loess", size = 0.3) +
   xlab("TopX") + ylab("% subangtrom models") +
   guides(color = guide_legend(ncol=2)) +
   geom_line(data=subset(subangstrom_data, (PDB == "Median") & (X <= 100)), colour="black", size=0.5) +
   ggtitle("Percentage of subangstrom models over TopX with varying X") +
   theme(plot.title = element_text(size=11))

ggsave('percentage_subangstrom_over_top_X_limit_100.png');

a <- ggplot(subangstrom_data, aes(x=X, Percentage, color=PDB)) +
   geom_smooth(method = "loess", size = 0.3) +
   xlab("TopX") + ylab("% subangtrom models") +
   guides(color = guide_legend(ncol=2)) +
   geom_line(data=subset(subangstrom_data, PDB == "Median"), colour="black", size=0.5) +
   ggtitle("Percentage of subangstrom models over TopX with varying X") +
   theme(plot.title = element_text(size=11))

ggsave('percentage_subangstrom_over_top_X.png');
'''


if __name__ == '__main__':

    try:
        arguments = docopt.docopt(__doc__.format(**locals()))

        output_directory = arguments['--output_directory']
        try:
            output_directory = os.path.abspath(os.path.expanduser(output_directory))
            assert(os.path.exists(output_directory))
        except:
            print('\nError: The path "{0}" could not be found.\n'.format(output_directory))
            sys.exit(1)

        try:
            expectn = int(arguments['--expectn'])
            assert(expectn > 0)
        except:
            print('\nError: expectn (the expected number of structures for case) must be a non-zero positive integer.\n')
            sys.exit(1)

        try:
            topx = int(arguments['--topx'])
            assert(topx > 0)
        except:
            print('\nError: TopX (the number of best-scoring structures to consider for certain metrics) must be a non-zero positive integer.\n')
            sys.exit(1)

        prefix = ''.join([c for c in arguments['--prefix'] if c.isalpha() or c.isdigit() or c in list(' _-@#%^&()[]<>=+')]).strip()
        if not prefix:
            print('\nError: The prefix "{0}" is invalid (some characters may have been removed for filesystem compatibility).\n'.format(structure_path))
            sys.exit(1)

        data_extraction_method_name = arguments['--data_extraction_method']
        if not data_extraction_method_name in data_extraction_methods:
            print('\nError: The data extraction method "{0}" could not be found in the script.\n'.format(data_extraction_method_name))
            sys.exit(1)
        data_extraction_method = data_extraction_methods[data_extraction_method_name]

        allowed_datasets = {
            '12_res' : '../input/short_loops.pdbs',
            '14_17_res' : '../input/long_loops.pdbs',
        }
        dataset = arguments['--dataset']
        if dataset not in allowed_datasets:
            raise Exception('The dataset "{0}" is not currently allowed. Valid arguments are: "{1}".'.format(dataset, '", "'.join(sorted(allowed_datasets.keys()))))
        dataset_list_file = allowed_datasets[dataset]

        test_mode = False
        if arguments['--test_mode']:
            expectn = 10
            test_mode = True

    except Exception, e:
        print('Failed while parsing arguments: %s.' % str(e))
        sys.exit(1)

    # Run the analysis
    extract_analysis_data(dataset_list_file, output_directory, data_extraction_method, expectn, topx, prefix, test_mode = test_mode)


