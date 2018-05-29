#!/usr/bin/env python3
# Copyright (c) 2015-2018 The Khronos Group Inc.
# Copyright (c) 2015-2018 Valve Corporation
# Copyright (c) 2015-2018 LunarG, Inc.
# Copyright (c) 2015-2018 Google Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# Author: Tobin Ehlis <tobine@google.com>
# Author: Dave Houlton <daveh@lunarg.com>

import argparse
import os
import sys
import platform
import json

# vk_validation_stats.py overview
#
# usage:
#    python vk_validation_stats.py [verbose]
#
#    Arguments:
#        verbose - enables verbose output, including VUID duplicates
#
# This script is intended to generate statistics on the state of validation code
#  based on information parsed from the source files and the database file
# Here's what it currently does:
#  1. Parse vk_validation_error_database.txt to store claimed state of validation checks
#  2. Parse validusage.json file to extract all VUIDs defined in the specification.
#  3. Parse source files to identify which checks are implemented and verify that this
#     exactly matches the list of checks claimed to be implemented in the database
#  4. Parse test file(s) and verify that reported tests exist
#  5. Report out stats on number of checks, implemented checks, and duplicated checks
#
# If a mis-match is found during steps 2, 3, or 4, then the script exits w/ a non-zero error code
#  otherwise, the script will exit(0)
#
# TODO:
#  1. Would also like to report out number of existing checks that don't yet use unique VUID strings
#  2. Could use notes to store custom fields (like TODO) and print those out here
#  3. Update test code to check if tests use unique VUID strings to check for errors instead of partial 
#     error message strings

db_file = '../layers/vk_validation_error_database.txt'
generated_layer_source_directories = [
'build',
'dbuild',
'release',
]
generated_layer_source_files = [
'parameter_validation.cpp',
'object_tracker.cpp',
]
layer_source_files = [
'../layers/core_validation.cpp',
'../layers/descriptor_sets.cpp',
'../layers/parameter_validation_utils.cpp',
'../layers/object_tracker_utils.cpp',
'../layers/shader_validation.cpp',
'../layers/buffer_validation.cpp',
]
header_file = '../layers/vk_validation_error_messages.h'
json_file = '../Vulkan-Headers/registry/validusage.json'
# TODO : Don't hardcode linux path format if we want this to run on windows
test_file = '../tests/layer_validation_tests.cpp'

# List of vuids that are allowed to be used more than once so don't warn on their duplicates
duplicate_exceptions = [
'"VUID-vkDestroyInstance-instance-00629"', # This covers the broad case that all child objects must be destroyed at DestroyInstance time
'"VUID-vkDestroyDevice-device-00378"', # This covers the broad case that all child objects must be destroyed at DestroyDevice time
'"VUID-VkCommandBufferBeginInfo-flags-00055"', # Obj tracker check makes sure non-null framebuffer is valid & CV check makes sure it's compatible w/ renderpass framebuffer
'"VUID-VkRenderPassCreateInfo-attachment-00833"', # This is an aliasing error that we report twice, for each of the two allocations that are aliasing
'"VUID-VkPipelineShaderStageCreateInfo-module-parameter"', # Covers valid shader module handle for both Compute & Graphics pipelines
'"VUID-VkMappedMemoryRange-memory-parameter"', # This is a case for VkMappedMemoryRange struct that is used by both Flush & Invalidate MappedMemoryRange
'"VUID-VkImageSubresource-aspectMask-parameter"', # This is a blanket case for all invalid image aspect bit errors. The spec link has appropriate details for all separate cases.
'"VUID-VkWriteDescriptorSet-descriptorType-00325"', # This is a descriptor set write update error that we use for a couple copy cases as well
'"VUID-vkCmdClearColorImage-image-00007"', # Handles both depth/stencil & compressed image errors for vkCmdClearColorImage()
'"VUID-vkCmdSetScissor-x-00595"', # Used for both x & y value of scissors to make sure they're not negative
'"VUID-VkSwapchainCreateInfoKHR-surface-parameter"', # Surface of VkSwapchainCreateInfoKHR must be valid when creating both single or shared swapchains
'"VUID-VkSwapchainCreateInfoKHR-oldSwapchain-parameter"', # oldSwapchain of VkSwapchainCreateInfoKHR must be valid when creating both single or shared swapchains
'"VUID-VkSwapchainCreateInfoKHR-imageFormat-01273"', # Single error for both imageFormat & imageColorSpace requirements when creating swapchain
'"VUID-VkWriteDescriptorSet-descriptorType-00330"', # Used twice for the same error codepath as both a param & to set a variable, so not really a duplicate
]

class ValidationDatabase:
    def __init__(self, filename=db_file):
        self.db_file = filename
        self.delimiter = '~^~'
        self.db_dict = {} # complete dict of all db values per error vuid
        # specialized data structs with slices of complete dict
        self.db_vuid_to_tests = {}              # dict where vuid is key to lookup list of tests implementing the vuid
        self.db_implemented_vuids = set()       # set of all error vuids claiming to be implemented in database file
        self.db_unimplemented_implicit = set()  # set of all implicit checks that aren't marked implemented
        self.db_invalid_implemented = set()     # set of checks with invalid check_implemented flags
    def read(self, verbose):
        """Read a database file into internal data structures, format of each line is <error_enum><check_implemented><testname><api><vuid_string><core|ext><errormsg><note>"""
        with open(self.db_file, "r", encoding="utf8") as infile:
            for line in infile:
                line = line.strip()
                if line.startswith('#') or '' == line:
                    continue
                db_line = line.split(self.delimiter)
                if len(db_line) != 8:
                    print("ERROR: Bad database line doesn't have 8 elements: %s" % (line))
                error_enum = db_line[0]
                implemented = db_line[1]
                testname = db_line[2]
                api = db_line[3]
                vuid_string = db_line[4]
                core_ext = db_line[5]
                error_str = db_line[6]
                note = db_line[7]
                # Read complete database contents into our class var for later use
                self.db_dict[vuid_string] = {}
                self.db_dict[vuid_string]['error_enum'] = error_enum
                self.db_dict[vuid_string]['check_implemented'] = implemented
                self.db_dict[vuid_string]['testname'] = testname
                self.db_dict[vuid_string]['api'] = api
                # self.db_dict[vuid_string]['vuid_string'] = vuid_string
                self.db_dict[vuid_string]['core_ext'] = core_ext
                self.db_dict[vuid_string]['error_string'] = error_str
                self.db_dict[vuid_string]['note'] = note
                # Now build custom data structs
                if 'Y' == implemented:
                    self.db_implemented_vuids.add(vuid_string)
                elif 'implicit' in note: # only make note of non-implemented implicit checks
                    self.db_unimplemented_implicit.add(vuid_string)
                if implemented not in ['Y', 'N']:
                    self.db_invalid_implemented.add(vuid_string)
                if testname.lower() not in ['unknown', 'none', 'nottestable']:
                    self.db_vuid_to_tests[vuid_string] = testname.split(',')
                    #if len(self.db_vuid_to_tests[error_enum]) > 1:
                    #    print "Found check %s that has multiple tests: %s" % (error_enum, self.db_vuid_to_tests[error_enum])
                    #else:
                    #    print "Check %s has single test: %s" % (error_enum, self.db_vuid_to_tests[error_enum])
                #unique_id = int(db_line[0].split('_')[-1])
                #if unique_id > max_id:
                #    max_id = unique_id
        if verbose:
            print("Found %d total VUIDs in database" % (len(self.db_dict.keys())))
            print("Found %d VUIDs in database marked as mplemented" % (len(self.db_implemented_vuids)))
            print("Found %d VUIDs in database marked as having a test implemented" % (len(self.db_vuid_to_tests.keys())))

#class ValidationHeader:
#    def __init__(self, filename=header_file):
#        self.filename = header_file
#        self.enums = []
#    def read(self, verbose):
#        """Read unique error enum header file into internal data structures"""
#        grab_enums = False
#        with open(self.filename, "r") as infile:
#            for line in infile:
#                line = line.strip()
#                if 'enum UNIQUE_VALIDATION_ERROR_CODE {' in line:
#                    grab_enums = True
#                    continue
#                if grab_enums:
#                    if 'VALIDATION_ERROR_MAX_ENUM' in line:
#                        grab_enums = False
#                        break # done
#                    elif 'kVUIDUndefined' in line:
#                        continue
#                    elif 'VALIDATION_ERROR_' in line:
#                        enum = line.split(' = ')[0]
#                        self.enums.append(enum)
#        if verbose:
#            print("Found %d error enums. First is %s and last is %s." % (len(self.enums), self.enums[0], self.enums[-1]))

class ValidationJSON:
    def __init__(self, filename=json_file):
        self.filename = json_file
        self.vuids = set()

    # Walk the JSON-derived dict and find all "vuid" key values
    def ExtractVUIDs(self, d):
        if hasattr(d, 'items'):
            for k, v in d.items():
                if k == "vuid":
                    yield v
                elif isinstance(v, dict):
                    for s in self.ExtractVUIDs(v):
                        yield s
                elif isinstance (v, list):
                    for l in v:
                        for s in self.ExtractVUIDs(l):
                            yield s
    def read(self, verbose):
        if os.path.isfile(self.filename):
            json_file = open(self.filename, 'r')
            self.json_dict = json.load(json_file)
            json_file.close()
        if len(self.json_dict) == 0:
            print("Error: Could not find, or error loading validusage.json")
            sys.exit(-1)
        # Extract all the vuid strings from the JSON db
        for vuid_string in self.ExtractVUIDs(self.json_dict):
            self.vuids.add(vuid_string)

class ValidationSource:
    def __init__(self, source_file_list, generated_source_file_list, generated_source_directories):
        self.source_files = source_file_list
        self.generated_source_files = generated_source_file_list
        self.generated_source_dirs = generated_source_directories

        if len(self.generated_source_files) > 0:
            qualified_paths = []
            for source in self.generated_source_files:
                for build_dir in self.generated_source_dirs:
                    filepath = '../%s/layers/%s' % (build_dir, source)
                    if os.path.isfile(filepath):
                        qualified_paths.append(filepath)
                        break
            if len(self.generated_source_files) != len(qualified_paths):
                print("Error: Unable to locate one or more of the following source files in the %s directories" % (", ".join(generated_source_directories)))
                print(self.generated_source_files)
                print("Skipping documentation validation test")
                exit(1)
            else:
                self.source_files.extend(qualified_paths)

        self.vuid_count_dict = {} # dict of vuid values to the count of how much they're used, and location of where they're used
    def parse(self, verbose):
        duplicate_checks = 0
        prepend = None
        for sf in self.source_files:
            line_num = 0
            with open(sf) as f:
                for line in f:
                    line_num = line_num + 1
                    if True in [line.strip().startswith(comment) for comment in ['//', '/*']]:
                        continue
                    # Find vuids
                    if prepend != None:
                        line = prepend[:-2] + line.lstrip().lstrip('"') # join lines skipping CR, whitespace and trailing/leading quote char
                        prepend = None
                    if '"VUID-' in line:
                        # Need to isolate the validation error text
                        #print("Line has check:%s" % (line))
                        line_list = line.split()

                        # A VUID string that has been broken by clang will start with "VUID- and end with -, and will be last in the list
                        broken_vuid = line_list[-1]
                        if broken_vuid.startswith('"VUID-') and broken_vuid.endswith('-"'):
                            prepend = line
                            continue
                     
                        vuid_list = []
                        for str in line_list:
                            if '"VUID-' in str: #and True not in [ignore_str in str for ignore_str in ['[VALIDATION_ERROR_', 'kVUIDUndefined', 'UNIQUE_VALIDATION_ERROR_CODE']]:
                                vuid_list.append(str.strip(',);{}"'))
                                #break
                        for vuid in vuid_list:
                            if vuid not in self.vuid_count_dict:
                                self.vuid_count_dict[vuid] = {}
                                self.vuid_count_dict[vuid]['count'] = 1
                                self.vuid_count_dict[vuid]['file_line'] = []
                                self.vuid_count_dict[vuid]['file_line'].append('%s,%d' % (sf, line_num))
                                #print "Found enum %s implemented for first time in file %s" % (enum, sf)
                            else:
                                self.vuid_count_dict[vuid]['count'] = self.vuid_count_dict[vuid]['count'] + 1
                                self.vuid_count_dict[vuid]['file_line'].append('%s,%d' % (sf, line_num))
                                #print "Found enum %s implemented for %d time in file %s" % (enum, self.enum_count_dict[enum], sf)
                                duplicate_checks = duplicate_checks + 1
                        #else:
                            #print("Didn't find actual check in line:%s" % (line))
        if verbose:
            print("Found %d unique implemented checks and %d are duplicated at least once" % (len(self.vuid_count_dict.keys()), duplicate_checks))

# Class to parse the validation layer test source and store testnames
# TODO: Enhance class to detect use of unique VUIDs in the test
class TestParser:
    def __init__(self, test_file_list, test_group_name=['VkLayerTest', 'VkPositiveLayerTest', 'VkWsiEnabledLayerTest']):
        self.test_files = test_file_list
        self.test_to_errors = {} # Dict where testname maps to list of vuids found in that test
        self.test_trigger_txt_list = []
        for tg in test_group_name:
            self.test_trigger_txt_list.append('TEST_F(%s' % tg)
            #print('Test trigger test list: %s' % (self.test_trigger_txt_list))

    # Parse test files into internal data struct
    def parse(self):
        # For each test file, parse test names into set
        grab_next_line = False # handle testname on separate line than wildcard
        testname = ''
        prepend = None
        for test_file in self.test_files:
            with open(test_file) as tf:
                for line in tf:
                    if True in [line.strip().startswith(comment) for comment in ['//', '/*']]:
                        continue

                    # if line ends in a broken VUID string, fix that before proceeding
                    if prepend != None:
                        line = prepend[:-2] + line.lstrip().lstrip('"') # join lines skipping CR, whitespace and trailing/leading quote char
                        prepend = None
                    if '"VUID-' in line:
                        line_list = line.split()
                        # A VUID string that has been broken by clang will start with "VUID- and end with -, and will be last in the list
                        broken_vuid = line_list[-1]
                        if broken_vuid.startswith('"VUID-') and broken_vuid.endswith('-"'):
                            prepend = line
                            continue
                     
                    if True in [ttt in line for ttt in self.test_trigger_txt_list]:
                        #print('Test wildcard in line: %s' % (line))
                        testname = line.split(',')[-1]
                        testname = testname.strip().strip(' {)')
                        #print('Inserting test: "%s"' % (testname))
                        if ('' == testname):
                            grab_next_line = True
                            continue
                        self.test_to_errors[testname] = []
                    if grab_next_line: # test name on its own line
                        grab_next_line = False
                        testname = testname.strip().strip(' {)')
                        self.test_to_errors[testname] = []
                    if '"VUID-' in line:
                        line_list = line.split()
                        for sub_str in line_list:
                            if '"VUID-' in sub_str:
                                #print("Trying to add vuids for line: %s" % ())
                                #print("Adding vuid %s to test %s" % (sub_str.strip(',);'), testname))
                                self.test_to_errors[testname].append(sub_str.strip(',);"'))

# Little helper class for coloring cmd line output
class bcolors:

    def __init__(self):
        self.GREEN = '\033[0;32m'
        self.RED = '\033[0;31m'
        self.YELLOW = '\033[1;33m'
        self.ENDC = '\033[0m'
        if 'Linux' != platform.system():
            self.GREEN = ''
            self.RED = ''
            self.YELLOW = ''
            self.ENDC = ''

    def green(self):
        return self.GREEN

    def red(self):
        return self.RED

    def yellow(self):
        return self.YELLOW

    def endc(self):
        return self.ENDC

def main(argv):
    result = 0 # Non-zero result indicates an error case
    verbose_mode = 'verbose' in sys.argv
    # parse db
    val_db = ValidationDatabase()
    val_db.read(verbose_mode)
    ## parse header
    #val_header = ValidationHeader()
    #val_header.read(verbose_mode)
    # parse validusage json
    val_json = ValidationJSON()
    val_json.read(verbose_mode)
    print("Found %d unique error vuids in validusage.json file." % len(val_json.vuids))
    # Create parser for layer files
    val_source = ValidationSource(layer_source_files, generated_layer_source_files, generated_layer_source_directories)
    val_source.parse(verbose_mode)
    print("Found %d unique error vuids in validation source code files." % len(val_source.vuid_count_dict))
    # Parse test files
    test_parser = TestParser([test_file, ])
    test_parser.parse()
    print("Found %d unique error vuids in test file %s." % (len(val_source.vuid_count_dict), test_file))

    # Process stats - Just doing this inline in main, could make a fancy class to handle
    #   all the processing of data and then get results from that
    txt_color = bcolors()
    if verbose_mode:
        print("Validation Statistics")
    else:
        print("Validation/Documentation Consistency Test")
    # First give number of checks in db & header and report any discrepancies
    num_db_vuids = len(val_db.db_dict.keys())
    num_json_vuids = len(val_json.vuids)
    if verbose_mode:
        print(" Database file includes %d unique checks" % (num_db_vuids))
        print(" Validusage.json file declares %d unique checks" % (num_json_vuids))

    # Report any checks that have an invalid check_implemented flag
    if len(val_db.db_invalid_implemented) > 0:
        result = 1
        print(txt_color.red() + "The following checks have an invalid check_implemented flag (must be 'Y' or 'N'):" + txt_color.endc())
        for invalid_imp_vuid in val_db.db_invalid_implemented:
            check_implemented = val_db.db_dict[invalid_imp_vuid]['check_implemented']
            print(txt_color.red() + "    %s has check_implemented flag '%s'" % (invalid_imp_vuid, check_implemented) + txt_color.endc())

    # Report details about how well the Database and Header are synchronized.
    db_keys = set(val_db.db_dict.keys())
    db_missing = val_json.vuids.difference(db_keys)
    json_missing = db_keys.difference(val_json.vuids)
    if num_db_vuids == num_json_vuids and len(db_missing) == 0 and len(json_missing) == 0:
        if verbose_mode:
            print(txt_color.green() + "  Database and validusage.json match, GREAT!" + txt_color.endc())
    else:
        print(txt_color.red() + "  Uh oh, Database doesn't match validusage.json file :(" + txt_color.endc())
        result = 1
        if len(db_missing) != 0:
            print(txt_color.red() + "   The following checks are in validusage.json but missing from database:" + txt_color.endc())
            for missing_vuid in db_missing:
                print(txt_color.red() + "    %s" % (missing_vuid) + txt_color.endc())
        if len(json_missing) != 0:
            print(txt_color.red() + "   The following checks are in database but aren't declared in the validusage.json file:" + txt_color.endc())
            for extra_vuid in json_missing:
                print(txt_color.red() + "    %s" % (extra_vuid) + txt_color.endc())

    # Report out claimed implemented checks vs. found actual implemented checks
#    imp_not_found = [] # Checks claimed to implemented in DB file but no source found
#    imp_not_claimed = [] # Checks found implemented but not claimed to be in DB
    multiple_uses = False # Flag if any vuids are used multiple times

    imp_vuids = set(val_source.vuid_count_dict.keys())
    imp_not_found = val_db.db_implemented_vuids.difference(imp_vuids)
    imp_not_claimed = imp_vuids.difference(val_db.db_implemented_vuids)
    for src_vuid in val_source.vuid_count_dict:
        if val_source.vuid_count_dict[src_vuid]['count'] > 1 and src_vuid not in duplicate_exceptions:
            multiple_uses = True
    if verbose_mode:
        print(" Database file claims that %d checks (%s) are implemented in source." % (len(val_db.db_implemented_vuids), "{0:.0f}%".format(float(len(val_db.db_implemented_vuids))/num_db_vuids * 100)))

    if len(val_db.db_unimplemented_implicit) > 0 and verbose_mode:
        print(" Database file claims %d implicit checks (%s) that are not implemented." % (len(val_db.db_unimplemented_implicit), "{0:.0f}%".format(float(len(val_db.db_unimplemented_implicit))/num_db_vuids * 100)))
        total_checks = len(val_db.db_implemented_vuids) + len(val_db.db_unimplemented_implicit)
        print(" If all implicit checks are handled by parameter validation this is a total of %d (%s) checks covered." % (total_checks, "{0:.0f}%".format(float(total_checks)/num_db_vuids * 100)))
    if len(imp_not_found) == 0 and len(imp_not_claimed) == 0:
        if verbose_mode:
            print(txt_color.green() + "  All claimed Database implemented checks have been found in source, and no source checks aren't claimed in Database, GREAT!" + txt_color.endc())
    else:
        result = 1
        print(txt_color.red() + "  Uh oh, Database claimed implemented don't match Source :(" + txt_color.endc())
        if len(imp_not_found) != 0:
            print(txt_color.red() + "   The following %d checks are claimed to be implemented in Database, but weren't found in source:" % (len(imp_not_found)) + txt_color.endc())
            for not_imp_vuid in imp_not_found:
                print(txt_color.red() + "    %s" % (not_imp_vuid) + txt_color.endc())
        if len(imp_not_claimed) != 0:
            print(txt_color.red() + "   The following checks are implemented in source, but not claimed to be in Database:" + txt_color.endc())
            for imp_vuid in imp_not_claimed:
                print(txt_color.red() + "    %s" % (imp_vuid) + txt_color.endc())

    if multiple_uses and verbose_mode:
        print(txt_color.yellow() + "  Note that some checks are used multiple times. These may be good candidates for new valid usage spec language." + txt_color.endc())
        print(txt_color.yellow() + "  Here is a list of each check used multiple times with its number of uses:" + txt_color.endc())
        for vuid in val_source.vuid_count_dict:
            if val_source.vuid_count_dict[vuid]['count'] > 1 and vuid not in duplicate_exceptions:
                print(txt_color.yellow() + "   %s: %d uses in file,line:" % (vuid, val_source.vuid_count_dict[vuid]['count']) + txt_color.endc())
                for file_line in val_source.vuid_count_dict[vuid]['file_line']:
                    print(txt_color.yellow() + "   \t%s" % (file_line) + txt_color.endc())

    # Now check that tests claimed to be implemented are actual test names
    bad_testnames = []
    tests_missing_vuid = {} # Report tests that don't use a VUID to check for error case
    for vuid in val_db.db_vuid_to_tests:
        for testname in val_db.db_vuid_to_tests[vuid]:
            if testname not in test_parser.test_to_errors:
                bad_testnames.append(testname)
            else:
                vuid_found = False
                for test_vuid in test_parser.test_to_errors[testname]:
                    if test_vuid == vuid:
                        #print("Found test that correctly checks for vuid: %s" % (vuid))
                        vuid_found = True
                if not vuid_found:
                    #print("Test %s is not using vuid %s to check for error" % (testname, vuid))
                    if testname not in tests_missing_vuid:
                        tests_missing_vuid[testname] = []
                    tests_missing_vuid[testname].append(vuid)
    if tests_missing_vuid and verbose_mode:
        print(txt_color.yellow() + "  \nThe following tests do not use their reported vuids to check for the validation error. You may want to update these to pass the expected vuid text to SetDesiredFailureMsg:" + txt_color.endc())
        for testname in tests_missing_vuid:
            print(txt_color.yellow() + "   Testname %s does not explicitly check for these ids:" % (testname) + txt_color.endc())
            for vuid in tests_missing_vuid[testname]:
                print(txt_color.yellow() + "    %s" % (vuid) + txt_color.endc())

    # TODO : Go through all vuids found in the test file and make sure they're correctly documented in the database file
    if verbose_mode:
        print(" Database file claims that %d checks have tests written." % len(val_db.db_vuid_to_tests))
    if len(bad_testnames) == 0:
        if verbose_mode:
            print(txt_color.green() + "  All claimed tests have valid names. That's good!" + txt_color.endc())
    else:
        print(txt_color.red() + "  The following testnames in Database appear to be invalid:")
        result = 1
        for bt in bad_testnames:
            print(txt_color.red() + "   %s" % (bt) + txt_color.endc())

    return result

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

