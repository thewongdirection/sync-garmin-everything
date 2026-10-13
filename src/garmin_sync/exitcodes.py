"""Process exit codes, so a scheduler or the Claude routine can tell failures apart."""

OK = 0
ERROR = 1  # unexpected failure, or records that failed to write
USAGE = 2  # bad command line (argparse's own code)
AUTH = 3  # a login is needed: run `garmin-sync auth garmin` / `auth google login`
INCOMPLETE = 4  # stopped early (rate limit, Garmin unreachable) or part of the work failed
