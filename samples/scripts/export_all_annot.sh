#!/bin/bash

# Sample script to export all annotated slices as SVG files
# Usage: TASK_ID DEST_PATH
TASK_ID=${1?}
DEST_PATH=${2?}

# Generate a listing of slides for this task
rm -rf /tmp/slides.csv
flask slides-list --min-paths 1 -C /tmp/slides.csv $TASK_ID

# Read all the slides
for line in $(cat /tmp/slides.csv); do

  # Read the CSV line
  IFS=',' read -r ID BLOCK_ID SECTION SLIDE STAIN S_NAME S_EXT NP NM SPECIMEN BLOCK <<< "$line"

  # Generate output filename
  fn_out=$DEST_PATH/${SPECIMEN}_${BLOCK}_annot_${SECTION}_${SLIDE}_${STAIN}__${S_NAME}.svg

  # Write the svg
  flask annot-export-svg --strip-width 1000 $TASK_ID $ID $fn_out
  echo "Wrote $fn_out"

done

