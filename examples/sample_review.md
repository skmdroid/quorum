### 🤖 Quorum review — **REQUEST_CHANGES**

This PR introduces multiple critical security vulnerabilities that must be resolved before merging: arbitrary code execution via `eval()`, OS command injection with `shell=True`, and a hardcoded credential in the source. Beyond the security issues, the new function lacks any test coverage and calls an undefined function `do_thing()`, and exception handling uses bare `except` clauses that will mask control-flow signals. Please address the critical security findings, add comprehensive tests, and fix the code quality issues before resubmission.

**3** critical · **5** high · **3** medium · **4** low

- ⛔ **[critical]** Arbitrary code execution via eval() — `app/handler.py:4` _(security)_
  - eval(cmd) executes attacker-controlled input as Python code. Any value reaching cmd grants full remote code execution in the process context.
  - ↳ _Remove eval entirely. If you must map strings to behavior, use an explicit allowlist/dispatch dict (e.g. {'start': start_fn}[cmd]) or ast.literal_eval for pure data parsing._
- ⛔ **[critical]** OS command injection via shell=True — `app/handler.py:5` _(security)_
  - subprocess.run(cmd, shell=True) passes cmd to a shell, so shell metacharacters (;, |, &&, $()) in cmd allow arbitrary OS command execution.
  - ↳ _Pass an argument list and avoid the shell: subprocess.run(shlex.split(cmd), shell=False). Validate/allowlist the command and never interpolate untrusted input into a shell string._
- ⛔ **[critical]** New function has no test coverage — `app/handler.py` _(tests)_
  - The run() function is newly added but no tests are provided. The function has multiple critical code paths: eval() execution, subprocess.run() with shell=True, and exception handling that must be tested.
  - ↳ _Add test file with cases for: (1) eval() success and failure paths, (2) subprocess execution with various inputs, (3) do_thing() success and exception paths_
- 🔴 **[high]** Call to undefined function do_thing() — `app/handler.py:7` _(correctness)_
  - do_thing() is never imported or defined in this module. At runtime this raises NameError, which is immediately swallowed by the bare except, so the call silently never succeeds.
  - ↳ _Import or define do_thing() before calling it, and verify the name resolves._
- 🔴 **[high]** Bare except swallows all exceptions including control-flow signals — `app/handler.py:8` _(correctness)_
  - A bare `except:` catches everything, including KeyboardInterrupt and SystemExit, and discards the exception entirely, printing only "failed". This hides the real error (e.g. the NameError from do_thing()) and makes the process impossible to interrupt cleanly. There is also no way for callers to detect that the operation failed.
  - ↳ _Catch a specific exception type (e.g. `except Exception as e:`), log/re-raise the error, and avoid masking control-flow exceptions._
- 🔴 **[high]** Hardcoded credential in source — `app/handler.py:3` _(security)_
  - password="hunter2" embeds a secret default directly in code. Committed secrets leak via VCS history and apply silently whenever the caller omits the argument.
  - ↳ _Remove the hardcoded default. Require the password explicitly or load it from an environment variable / secrets manager (e.g. os.environ['APP_PASSWORD'])._
- 🔴 **[high]** eval() behavior missing edge-case tests — `app/handler.py:4` _(tests)_
  - eval(cmd) executes without tests covering failure cases. Missing tests for SyntaxError, NameError, and other eval() exceptions.
  - ↳ _Add tests: valid Python expression, invalid syntax, undefined variables, and expressions raising exceptions_
- 🔴 **[high]** subprocess.run() untested with edge cases — `app/handler.py:5` _(tests)_
  - subprocess execution with shell=True is not tested. Missing coverage for: empty/None commands, return codes, stderr/stdout, and shell metacharacters.
  - ↳ _Add tests covering normal execution, non-zero exit codes, subprocess exceptions, and command edge cases_
- 🟡 **[medium]** cmd used inconsistently as Python expression and shell command — `app/handler.py:4` _(correctness)_
  - Line 4 evaluates cmd as a Python expression via eval(), while line 5 passes the same cmd to subprocess.run with shell=True, treating it as a shell command string. A single value cannot be both a valid Python expression and the intended shell command; whichever interpretation the caller intends, the other call will misbehave or error.
  - ↳ _Decide on a single semantics for cmd and remove the redundant/contradictory evaluation path._
- 🟡 **[medium]** Bare except clause — `app/handler.py:8` _(style)_
  - Using bare 'except:' makes it unclear what exceptions are being handled and complicates debugging
  - ↳ _Catch specific exceptions, e.g., 'except Exception as e:' or specify the exception type(s)_
- 🟡 **[medium]** Bare except clause lacks exception-type coverage — `app/handler.py:8` _(tests)_
  - Exception handling catches all exceptions equally, but no tests verify behavior for different exception types (ValueError, AttributeError, etc.) from do_thing().
  - ↳ _Add tests triggering different exception types from do_thing() to verify they all reach the except block and print as expected_
- 🔵 **[low]** result assigned but never used — `app/handler.py:4` _(correctness)_
  - The return value of eval(cmd) is stored in `result` but never read, so the evaluation's result is silently discarded. Either the eval call is pointless or the function is missing a `return result`.
  - ↳ _Return or use `result`, or remove the assignment if the evaluation is not needed._
- 🔵 **[low]** Unused variable assignment — `app/handler.py:4` _(style)_
  - Variable 'result' is assigned but never used
  - ↳ _Remove the unused assignment or use the variable_
- 🔵 **[low]** Unused function parameter — `app/handler.py:3` _(style)_
  - Parameter 'password' is never used within the function body
  - ↳ _Remove the unused 'password' parameter if not needed_
- 🔵 **[low]** Informal error logging — `app/handler.py:9` _(style)_
  - Using print() for error output instead of a structured logging mechanism
  - ↳ _Use Python's logging module: 'logging.error(...)' with appropriate context_

