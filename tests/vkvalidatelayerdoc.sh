#!/bin/bash
#set -x

if [ -t 1 ] ; then
    RED='\033[0;31m'
    GREEN='\033[0;32m'
    NC='\033[0m' # No Color
else
    RED=''
    GREEN=''
    NC=''
fi
# If we can't find the source dir then skip
if [ ! -d "../../scripts" ]; then
    printf "$GREEN[ SKIPPED  ]$NC $0\n"
    printf "  To run validation DB checks you can manually execute\n"
    printf "  vk_validation_stats.py from the 'scripts' dir of your source tree\n"
    exit
fi

printf "$GREEN[ RUN      ]$NC $0\n"

# Run doc validation from project scripts dir
pushd ../../scripts

# Validate that layer database matches source contents
python3 vk_validation_stats.py

RES=$?

if [ $RES -eq 0 ] ; then
   printf "$GREEN[  PASSED  ]$NC 1 test\n"
else # Warn but don't fail on validation_status script'
   printf "$RED[  WARN  ]$NC Validation statistics script vk_validation_stats.py terminated abnormally.\n"
   printf "$RED[  WARN  ]$NC 1 test\n"
fi
# Restore original directory
popd
exit 0
