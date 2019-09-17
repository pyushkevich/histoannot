**********************************************
Notes: Tanglethon Validation
**********************************************

These are just some notes on how PHAS was used to validate deep learning-based tangle counting in IHC slides stained for tau protein.

Create a new task for validation
--------------------------------

First, create a new labelset that will be used for this::
    flask labelset-add -d "Tanglethon validation" tangleval samples/labelsets/tangleval.json

Then add the actual task::
    flask tasks-add --json samples/tasks/tangleval.json


Generate actual samples at random from curves
---------------------------------------------
This is the command::
    flask samples-random-from-annot -r 100 -n 40 -u pyushkevich -c 1 3 candidate_roi 2048

Notes:
* 100 is the standard deviation of the random offset of the box center from curves
* 40 is the number of boxes per slide to generate
* 1 is the ID of the annotation task used to draw the curves
* 3 is the ID of the newly added task
* candidate_roi is the label assigned to new boxes
* `-c` clobbers existing boxes with label `candidate_roi`
* 2048 is the size of the box
    
