#!/usr/bin/env python

#C: THIS FILE IS PART OF THE CYLC SUITE ENGINE.
#C: Copyright (C) 2008-2014 Hilary Oliver, NIWA
#C:
#C: This program is free software: you can redistribute it and/or modify
#C: it under the terms of the GNU General Public License as published by
#C: the Free Software Foundation, either version 3 of the License, or
#C: (at your option) any later version.
#C:
#C: This program is distributed in the hope that it will be useful,
#C: but WITHOUT ANY WARRANTY; without even the implied warranty of
#C: MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#C: GNU General Public License for more details.
#C:
#C: You should have received a copy of the GNU General Public License
#C: along with this program.  If not, see <http://www.gnu.org/licenses/>.

import re
from isodatetime.parsers import TimePointParser, TimeIntervalParser
from cylc.time_parser import CylcTimeParser
from cylc.cycling import PointBase, IntervalBase

# TODO - RESTORE MEMOIZATION FOR TIME POINT COMPARISONS
# TODO - Consider copy vs reference of points, intervals, sequences
# TODO - Use context points properly
# TODO - ignoring anchor in back-compat sections


CYCLER_TYPE_ISO8601 = "iso8601"
CYCLER_TYPE_SORT_KEY_ISO8601 = "b"

MEMOIZE_LIMIT = 10000



class ISO8601Point(PointBase):

    """A single point in an ISO8601 date time sequence."""

    TYPE = CYCLER_TYPE_ISO8601
    TYPE_SORT_KEY = CYCLER_TYPE_SORT_KEY_ISO8601

    def cmp_(self, other):
        return iso_point_cmp(self.value, other.value)

    def sub(self, other):
        if isinstance(other, ISO8601Point):
            return ISO8601Interval(
                iso_point_sub_point(self.value, other.value))
        return ISO8601Point(
            iso_point_sub_interval(self.value, other.value))

    def add(self, other):
        return iso_point_add(self.value, other.value)


class ISO8601Interval(IntervalBase):

    """The interval between points in an ISO8601 date time sequence."""

    NULL_INTERVAL_STRING = "P0Y"
    TYPE = CYCLER_TYPE_ISO8601
    TYPE_SORT_KEY = CYCLER_TYPE_SORT_KEY_ISO8601

    @classmethod
    def get_null(cls):
        return ISO8601Interval("P0Y")

    def __mul__(self, m):
        # the suite runahead limit is a multiple of the smallest sequence interval
        return iso_interval_mul(self.value, m)

    def __abs__(self):
        return iso_interval_abs(self.value, self.NULL_INTERVAL_STRING)

    def cmp_(self, other):
        return iso_interval_cmp(self.value, other.value)

    def sub(self, other):
        return ISO8601Interval(iso_interval_sub(self.value, other.value))

    def add(self, other):
        if isinstance(other, ISO8601Interval):
            return ISO8601Interval(
                iso_interval_add_interval(self.value, other.value))
        return ISO8601Point(
                iso_point_add(other.value, self.value))


class ISO8601Sequence(object):
    """
    A sequence of ISO8601 date time points separated by an interval.
    """

    TYPE = CYCLER_TYPE_ISO8601
    TYPE_SORT_KEY = CYCLER_TYPE_SORT_KEY_ISO8601

    def __init__(self, dep_section, context_start_point=None, context_end_point=None):

        self.dep_section = dep_section

        self.context_start_point = ISO8601Point(context_start_point)
        self.context_end_point = ISO8601Point(context_end_point)

        self.offset = ISO8601Interval.get_null()

        i = None
        m = re.match('^Daily\(\s*(\d+)\s*,\s*(\d+)\s*\)$', dep_section)
        if m:
            # back compat Daily()
            anchor, step = m.groups()
            i = 'P' + step + 'D'
        else:
            m = re.match('^Monthly\(\s*(\d+)\s*,\s*(\d+)\s*\)$', dep_section)
            if m:
                # back compat Monthly()
                anchor, step = m.groups()
                i = 'P' + step + 'M'
            else:
                m = re.match('^Yearly\(\s*(\d+)\s*,\s*(\d+)\s*\)$', dep_section)
                if m:
                    # back compat Yearly()
                    anchor, step = m.groups()
                    i = 'P' + step + 'Y'
                else:
                    # ISO8601
                    i = dep_section
        if not i:
            raise "ERROR: iso8601 cycling init!"

        self.spec = i
        self.time_parser = CylcTimeParser(context_start_point, context_end_point)
        self.step = ISO8601Interval(i)
        self.recurrence = self.time_parser.parse_recurrence(i)
        self._recurrence_str = str(self.recurrence)

    def set_offset(self, i):
        """Alter state to offset the entire sequence."""
        self.offset = ISO8601Interval(i)
        res = self.context_start_point + self.offset
        self.time_parser = CylcTimeParser(str(res), str(self.context_end_point))
        self.recurrence = self.time_parser.parse_recurrence(self.spec)
        self.value = str(self.recurrence)
 
    def get_offset(self):
        return self.offset

    def get_interval(self):
        return self.step

    def is_on_sequence(self, p):
        """Return True if p is on-sequence."""
        return self.recurrence.get_is_valid(point_parse(p.value))

    def get_prev_point(self, p):
        # may be None if out of the recurrence bounds
        res = None
        prv = self.recurrence.get_prev(point_parse(p.value))
        if prv:
            res = ISO8601Point(str(prv))
        return res

    def get_next_point(self, p):
        # may be None if out of the recurrence bounds
        res = None
        nxt = self.recurrence.get_next(point_parse(p.value))
        if nxt:
            res = ISO8601Point(str(nxt))
        return res

    def get_nexteq_point(self, p):
        """return the on-sequence point greater than or equal to p."""
        if self.TYPE != p.TYPE:
            return self.context_start_point
        p_iso_point = point_parse(p.value)
        for i, iso_point in enumerate(self.recurrence):
            if i == 0:
                if iso_point < p_iso_point:
                    return self.context_start_point
            elif iso_point >= p_iso_point:
                return ISO8601Point(str(iso_point))
        return None

    def __eq__(self, other):
        if self.TYPE != other.TYPE:
            return False
        return self.value == other.value


def memoize(function):
    """This stores results for a given set of inputs to a function.

    The inputs and results of the function must be immutable.
    Keyword arguments are not allowed.

    To avoid memory leaks, only the first 10000 separate input
    permutations are cached for a given function.

    """
    inputs_results = {}
    def _wrapper(*args):
        try:
            return inputs_results[args]
        except KeyError:
            results = function(*args)
            if len(inputs_results) > MEMOIZE_LIMIT:
                # Full up, no more room.
                return results
            inputs_results[args] = results
            return results
    return _wrapper


@memoize
def iso_interval_abs(interval_string, other_interval_string):
    interval = interval_parse(interval_string)
    other = interval_parse(other_interval_string)
    if interval < other:
        return interval * -1


@memoize
def iso_interval_add(interval_string, other_interval_string):
    interval = interval_parse(interval_string)
    other = interval_parse(other_interval_string)
    return str(interval + other)


@memoize
def iso_interval_sub(interval_string, other_interval_string):
    interval = interval_parse(interval_string)
    other = interval_parse(other_interval_string)
    return str(interval - other)


@memoize
def iso_interval_mul(interval_string, factor):
    interval = interval_parse(interval_string)
    return str(interval * factor)


@memoize
def iso_interval_cmp(interval_string, other_interval_string):
    interval = interval_parse(interval_string)
    other = interval_parse(other_interval_string)
    return cmp(interval - other)


@memoize
def iso_point_cmp(point_string, other_point_string):
    point = point_parse(point_string)
    other_point = point_parse(other_point_string)
    return cmp(point, other_point)


@memoize
def iso_point_sub_interval(point_string, interval_string):
    point = point_parse(point_string)
    interval = interval_parse(interval_string)
    return str(point - interval)


@memoize
def iso_point_sub_point(point_string, other_point_string):
    point = point_parse(point_string)
    other_point = point_parse(other_point_string)
    return str(point - other_point)


@memoize
def iso_point_add(point_string, interval_string):
    point = point_parse(point_string)
    interval = interval_parse(interval_string)
    return str(point + interval)


interval_parser = TimeIntervalParser()
point_parser = TimePointParser()


def interval_parse(interval_string):
    try:
        return _interval_parse(interval_string).copy()
    except Exception:
        return -1 * _interval_parse(interval_string.replace("-", "")).copy()


@memoize
def _interval_parse(interval_string):
    return interval_parser.parse(interval_string)


def point_parse(point_string):
    return _point_parse(point_string).copy()


@memoize
def _point_parse(point_string):
    return point_parser.parse(point_string)


if __name__ == '__main__':
    p_start = ISO8601Point('20100808T00')
    p_stop = ISO8601Point('20100808T02')
    i = ISO8601Interval('PT6H')
    print p_start - i 
    print p_stop + i 

    print
    r = ISO8601Sequence('PT10M', str(p_start), str(p_stop),)
    r.set_offset(- ISO8601Interval('PT10M'))
    p = r.get_nexteq_point(ISO8601Point('20100808T0000'))
    print p
    while p and p < p_stop:
        print ' + ' + str(p), r.is_on_sequence(p)
        p = r.get_next_point(p)
    print 
    while p and p >= p_start:
        print ' + ' + str(p), r.is_on_sequence(p)
        p = r.get_prev_point(p)
     
    print
    print r.is_on_sequence(ISO8601Point('20100809T0005'))
