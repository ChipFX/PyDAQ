
Create yourself a python virtual environment and activate it.

Run `pip install -e .`  in there.

`daq` should now be in your path and linked to this git repo.

On a test on Windowd 11, venv&pip did not yield a working executable, and the following three packages needed to be installed:
pint
rich
pyserial
