#!/usr/bin/env python2

# The MIT License (MIT)
#
# Copyright (c) 2015 Kale Kundert
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

#$ -S /usr/bin/python
#$ -l mem_free=4G
#$ -l arch=linux-x64
#$ -l netapp=2G
#$ -cwd

import os
import sys; sys.path.append(os.getcwd())
import optparse
import subprocess
import re
import json
import gzip

from libraries import utilities
from libraries import settings; settings.load(interactive=False)
from libraries.dataController import DataController 

# Parse arguments.

if len(sys.argv) != 5 or 'SGE_TASK_ID' not in os.environ:
    print 'Usage: SGE_TASK_ID=<id> loop_benchmark.py <benchmark_id> <if_use_database> <if_complete_run> <if_use_native_structure>'
    sys.exit(1)

benchmark_id = int(sys.argv[1])
use_database = sys.argv[2]=='--use-database'
complete_run = sys.argv[3]=='--complete-run'
use_native_structure = sys.argv[4]=='--use-native-structure'
data_controller = DataController('database') if use_database else DataController('disk')
task_id = data_controller.read_task_completion_list(benchmark_id)[ int(os.environ['SGE_TASK_ID']) - 1 ] \
          if complete_run else int(os.environ['SGE_TASK_ID'])-1

# Figure out which loop to benchmark.

benchmark_define_dict = data_controller.get_benchmark_define_dict(benchmark_id)
script_path = benchmark_define_dict['script']
script_vars = benchmark_define_dict['vars']
flags_path = benchmark_define_dict['flags']
fragments_path = benchmark_define_dict['fragments']
fast = benchmark_define_dict['fast']
non_random = benchmark_define_dict['non_random']
input_pdbs = benchmark_define_dict['input_pdbs']
pdb_path = input_pdbs[task_id % len(input_pdbs)].pdb_path
pdb_name = os.path.basename(pdb_path).split('.')[0] 
pdb_tag = os.path.splitext(os.path.basename(pdb_path))[0]
loop_path = re.sub('\.pdb(\.gz)?$', '.loop', pdb_path)
structures_path = benchmark_define_dict['structures_path']

# Set LD_LIBRARY_PATH so that the MySQL libraries can be found.

rosetta_env = os.environ.copy()
mysql_lib = '/netapp/home/kbarlow/lib/mysql-connector-c-6.1.2-linux-glibc2.5-x86_64/lib:'

try:
    rosetta_env['LD_LIBRARY_PATH'] = mysql_lib + ':' + rosetta_env['LD_LIBRARY_PATH']
except KeyError:
    rosetta_env['LD_LIBRARY_PATH'] = mysql_lib

# Build the RosettaScripts command line.

rosetta_path = os.path.abspath(settings.rosetta)
rosetta_scripts = os.path.join(rosetta_path, 'source', 'bin', 'rosetta_scripts.mysql.linuxgccrelease')
rosetta_database = os.path.join(rosetta_path, 'database')

# This assumes that the script is being passed a structure in a folder with a sibling folder containing a reference structure with the same filename

reference_structure = os.path.join(os.path.split(os.path.split(pdb_path)[0])[0], 'reference', os.path.split(pdb_path)[1])
assert(os.path.exists(reference_structure))

rosetta_command = [
        rosetta_scripts,
        '-database', rosetta_database,
        '-in:file:s', pdb_path,
        '-parser:protocol', script_path,
        '-parser:script_vars',
            'loop_file={0}'.format(loop_path),
            'fast={0}'.format('yes' if fast else 'no'),
]         + script_vars

if use_database:
    rosetta_command += [
        '-in:file:native', reference_structure,
        '-out:nooutput',
        '-inout:dbms:mode', 'mysql',
        '-inout:dbms:database_name', settings.db_name,
        '-inout:dbms:user', settings.db_user,
        '-inout:dbms:password', settings.db_password,
        '-inout:dbms:host', settings.db_host,
        '-inout:dbms:port', settings.db_port,
    ]
else:
    rosetta_command += [
        '-out:prefix', structures_path+'/'+str(task_id)+'_',
        '-overwrite',
    ]

if use_native_structure:
    rosetta_command += ['-in:file:native',  reference_structure]

if flags_path is not None:
    rosetta_command += ['@', flags_path]

if fragments_path is not None:
    frag_file = os.path.join(fragments_path, '{0}A', '{0}A.200.{1}mers.gz')
    rosetta_command += [
            '-loops:frag_sizes', '9', '3', '1',
            '-loops:frag_files',
                frag_file.format(pdb_tag, 9),
                frag_file.format(pdb_tag, 3),
                'none',
    ]
if non_random:
    rosetta_command += ['-run:constant_seed', '-run:jran', task_id]

# Run the benchmark.

stdout, stderr = utilities.tee(rosetta_command, env=rosetta_env)

# Associate this run with the right benchmark and save log files.

protocol_match = re.search("protocol_id '([1-9][0-9]*)'", stdout)
protocol_id = protocol_match.groups()[0] if protocol_match else None

if not use_database:
    struct_path = os.path.join(structures_path, str(task_id)+'_'+pdb_name+'_0001.pdb') 
    data_controller.calc_rmsd(loop_path, reference_structure, struct_path)
    #compress the pdb file
    with open(struct_path, 'rb') as f_in:
        f_out = gzip.open(struct_path+'.gz', 'wb')
        f_out.writelines(f_in)
        f_out.close()    
    os.remove(struct_path)

data_controller.write_log(benchmark_id, protocol_id, stdout, stderr, task_id)

