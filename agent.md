# Coding best practices
- All imports at the top of the file
- All unit tests of a python file must be in test_{name_of_python_file.py}
- Every python file should have a respective unit test file. Except you have a really good reason not to.
- Assume that all keys are there. No unnecessary logic to avoid running into errors
- Encapsulate try and catch only when necessary at the highest level. Allow functions to fail and to escalade the error
- Unit test mocks are fine but also create tests that test the actual functionnality (end-to-end )
- Never change Telegram webhook configuration, call Telegram `setWebhook` or `deleteWebhook`, or otherwise retarget bot delivery unless the user explicitly asks for that change in the current turn.
- Functions should not be above 10-20 lines. There might be some exceptions but generally keep functions short and break logic down.

# Instructions
- If the request of the user requires substantial refactoring of the code, then first suggest the user an infrastructure change request
- use implementation and implementation_details to document potential changes in the architecture and implementation logic
- Do not make any changes in the migration .sql files of the migrations folder. If you want to change something create a 99_suggested_schema.sql