# Scalp Terminal — notes for Claude

## QA Register (keep it current, every time)

The QA Register is the Sheets artifact "Scalp Terminal — QA Register":
https://claude.ai/artifact/MADnNBvTA3C74Y34wPH55i (tabs: Test Cases, Defects, To-Do).

In the same piece of work, without being asked, update it whenever you:

- **add or change a test**: one row per test case on **Test Cases**, in plain English
  with the expected result. IDs are `TC-<AREA>-<NNNN>`. A new case takes the next number
  in its area, and existing IDs never change.
- **find or fix a defect**: one row on **Defects** with columns Defect ID (`DEF-NNN`),
  description, date/time found, app version where it occurred, machine
  (Anbu / Dhivakar / Others), fixed date, fixed-in version, Test Case IDs added,
  Test Case IDs tested.
- **plan or make a change, a feature or a fix**: a row on **To-Do** (`TD-NNN`), with its
  status moved along as the work goes.

The project doc `claude/qa-register-extract-tests.py` lists every test case found in the
code. Use it to check the Test Cases tab against the code.

If the Sheet cannot be reached in a session (no Claude Docs connector), say so in that
session, and list the rows that still need adding.
