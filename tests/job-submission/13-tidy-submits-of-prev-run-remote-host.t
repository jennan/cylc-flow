#!/bin/bash
# THIS FILE IS PART OF THE CYLC SUITE ENGINE.
# Copyright (C) 2008-2016 NIWA
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#-------------------------------------------------------------------------------
# Test tidy of submits of previous runs.
. "$(dirname "$0")/test_header"

CYLC_TEST_HOST=$( \
    cylc get-global-config -i '[test battery]remote host' \
    2>'/dev/null')
if [[ -z "${CYLC_TEST_HOST}" ]]; then
    skip_all '"[test battery]remote host": not defined'
fi

set_test_number 11
install_suite "${TEST_NAME_BASE}" "${TEST_NAME_BASE}"
run_ok "${TEST_NAME_BASE}-validate" \
    cylc validate "${SUITE_NAME}" -s "CYLC_TEST_HOST=${CYLC_TEST_HOST}"
suite_run_ok "${TEST_NAME_BASE}-run" \
    cylc run --debug --reference-test "${SUITE_NAME}" \
    -s "CYLC_TEST_HOST=${CYLC_TEST_HOST}"
RLOGD1="cylc-run/${SUITE_NAME}/log/job/1/t1/01"
RLOGD2="cylc-run/${SUITE_NAME}/log/job/1/t1/02"
LOGD1="$(cylc get-global-config --print-run-dir)/${SUITE_NAME}/log/job/1/t1/01"
LOGD2="$(cylc get-global-config --print-run-dir)/${SUITE_NAME}/log/job/1/t1/02"

SSH='ssh -n -oBatchMode=yes -oConnectTimeout=5'
run_ok "exists-rlogd1" ${SSH} "${CYLC_TEST_HOST}" test -e "${RLOGD1}"
run_ok "exists-rlogd2" ${SSH} "${CYLC_TEST_HOST}" test -e "${RLOGD2}"
exists_ok "${LOGD1}"
exists_ok "${LOGD2}"
sed -i 's/script =.*$/script = true/' "suite.rc"
sed -i -n '1,/triggered off/p' "reference.log"
suite_run_ok "${TEST_NAME_BASE}-run" \
    cylc run --debug --reference-test "${SUITE_NAME}" \
    -s "CYLC_TEST_HOST=${CYLC_TEST_HOST}"
run_ok "exists-rlogd1" ${SSH} "${CYLC_TEST_HOST}" test -e "${RLOGD1}"
run_fail "not-exists-rlogd2" ${SSH} "${CYLC_TEST_HOST}" test -e "${RLOGD2}"
exists_ok "${LOGD1}"
skip 1 '02-file-exists-fail - will fix as part of cylc/cylc#1880'
#exists_fail "${LOGD2}"
#-------------------------------------------------------------------------------
ssh -n -oBatchMode=yes -oConnectTimeout=5 "${CYLC_TEST_HOST}" \
    "rm -rf 'cylc-run/${SUITE_NAME}'"
purge_suite "${SUITE_NAME}"
exit