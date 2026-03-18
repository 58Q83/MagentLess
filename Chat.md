```
org: (str) - Organization name identifier from Github.
repo: (str) - Repository name identifier from Github.
number: (int) - The PR number.
state: (str) - The PR state.
title: (str) - The PR title.
body: (str) - The PR body.
base: (dict) - The target branch information of the PR
resolved_issues: (list) - A json list of strings that represent issues that resolved by PR application.
fix_patch: (str) - A fix-file patch that was contributed by the solution PR.
test_patch: (str) - A test-file patch that was contributed by the solution PR.
fixed_tests: (dict) - A json dict of strings that represent tests that should be fixed after the PR application.
p2p_tests: (dict) - The tests that should pass before and after the PR application.
f2p_tests: (dict) - The tests resolved by the PR and tied to the issue resolution.
s2p_tests: (dict) - The tests that should skip before the PR application, and pass after the PR application.
n2p_tests: (dict) - The tests that did not exist before the PR application and tests that should be passed after the PR application.
run_result: (dict) - Overall run results, including number of tests passed, number of tests failed, etc.
test_patch_result: (dict) -  The result after the test patch was applied.
fix_patch_result: (dict) - The result after all the patches were applied.
instance_id: (str) - A formatted instance identifier, usually as org__repo_PR-number.

```