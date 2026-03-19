# Coding best practices
- All imports at the top of the file
- All unit tests of a python file must be in test_{name_of_python_file.py}
- Every python file should have a respective unit test file. Except you have a really good reason not to.
- Assume that all keys are there. No unnecessary logic to avoid running into errors
- Encapsulate try and catch only when necessary at the highest level. Allow functions to fail and to escalade the error
- Unit test mocks are fine but also create tests that test the actual functionnality (end-to-end)