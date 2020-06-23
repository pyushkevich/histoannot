#!/bin/bash

# This script is used to preprocess images in a GCP bucket for use
# with PHAS, the PICSL Histology Annotation System.
function usage()
{
    cat <<-USAGE

    prep_bucket: image preprocessing script for PHAS
    usage: prep_bucket <command> [options] ARGS

    commands:

        process_image: generate pyramid/thumbnail for single image

            usage: prep_bucket process_image [options] SRCURL TRGURL

            SRCURL: Google Cloud Storage URL or path to the source image
            TRGURL: Google Cloud Storage URL or path to the folder where
                    the preprocessed images will be stored

            options:
              -j <value>     : Set JPEG compression level. Default: 80
              -t <value>     : Set thumbnail size. Default: 512
              -P             : Do not generate pyramid images. Do this if your 
                               raw data is already in OpenSlide-compatible format

        process_dir: process a directory of images

            usage: prep_bucket process_dir [options] SRCURL TRGURL

            SRCURL: Google Cloud Storage URL or path with source images. 
                    The path may also contain wildcards

            TRGURL: Google Cloud Storage URL or path for output

            options:
              -j <value>     : Set JPEG compression level. Default: 80
              -t <value>     : Set thumbnail size. Default: 512
              -P             : Do not generate pyramid images. Do this if your 
                               raw data is already in OpenSlide-compatible format
              -n             : No-clobber mode (existing results skipped)
              -k <file>      : Use Google service account credentials from file
                   
USAGE
}

# Common vars


# Process a folder. Takes as input a pair of gs:// urls for the path of the
# raw data, which must contain a flat list of image files readable by VIPS, 
# and path to the processed data. For every image in the source directory, 
# this will create a folder and place in it a pyramidal tiff file and a 
# thumbnail. These files will be given suffixes _pyramidal.tiff 
# and _thumb.tiff
function process_dir()
{
    local NOCLOBBER JPEGLEVEL THUMBSIZE SOURCE TARGET SRCLIST TRGLIST WORKDIR
    local PYRAMID THUMB FN_BASE FN_EXT FN_NOEXT CREDS SKIP_PYRAMID
    unset SKIP_PYRAMID
    unset NOCLOBBER
    JPEGLEVEL=80
    THUMBSIZE=512

    # Read optional arguments
    while getopts "nj:t:k:P" flag; do
        case "$flag" in
            n) NOCLOBBER=1;;
            j) JPEGLEVEL=$OPTARG;;
            t) THUMBSIZE=$OPTARG;;
            P) SKIP_PYRAMID=1;;
            k) gcloud auth activate-service-account --key-file $OPTARG;;
        esac
    done

    shift $((OPTIND-1))

    # Get the input and the output
    SOURCE=${1?}
    TARGET=${2?}

    WORKDIR=$(mktemp -d)
    SRCLIST=$WORKDIR/source.txt
    TRGLIST=$WORKDIR/target.txt

    # Get a list of all the images in the source directory
    gsutil ls $SOURCE | grep -v '\/$' > $SRCLIST

    # Get the target image list too
    if [[ $NOCLOBBER ]]; then
        gsutil ls $TARGET/** | grep -v '\/$' > $TRGLIST
    fi

    while IFS= read -r FN; do

        # Check if the file exists
        if [[ $NOCLOBBER ]]; then

            FN_BASE=$(basename $FN)
            FN_EXT=$(echo $FN_BASE | sed -e "s/.*\.//g")
            FN_NOEXT=$(basename $FN_BASE .${FN_EXT})

            if [[ $SKIP_PYRAMID ]]; then
                PYRAMID=""
            else
                PYRAMID="$TARGET/$FN_NOEXT/${FN_NOEXT}_pyramidal.tiff"
            fi

            THUMB="$TARGET/$FN_NOEXT/${FN_NOEXT}_thumb.png"

            if grep "$PYRAMID" $TRGLIST > /dev/null \
                && grep "$THUMB" $TRGLIST > /dev/null; then
                echo "Skipping image $FN_BASE"
                continue
            fi

        fi

        # Process the image
        if [[ $SKIP_PYRAMID ]]; then
            SKIP_PYRAMID_OPT="-P"
        else
            SKIP_PYRAMID_OPT=""
        fi

        process_image -j $JPEGLEVEL -t $THUMBSIZE $SKIP_PYRAMID_OPT \
            "$FN" "$TARGET/$FN_NOEXT/"

    done < $SRCLIST
}

# This function processes a single image
function process_image()
{
    local JPEGLEVEL THUMBSIZE SOURCE TARGET
    local FN_BASE FN_EXT FN_NOEXT WORKDIR IMG PTIFF THUMB
    local OPTIND OPTARG SKIP_PYRAMID
    unset SKIP_PYRAMID
    JPEGLEVEL=80
    THUMBSIZE=512

    # Read optional arguments
    while getopts "j:t:k:P" flag; do
        case "$flag" in
            j) JPEGLEVEL=$OPTARG;;
            t) THUMBSIZE=$OPTARG;;
            P) SKIP_PYRAMID=1;;
            k) gcloud auth activate-service-account --key-file $OPTARG;;
        esac
    done
    shift $((OPTIND-1))

    # Get the source and destination images
    SOURCE=${1?}
    TARGET=${2?}

    # Get the filename components
    FN_BASE=$(basename $SOURCE)
    FN_EXT=$(echo $FN_BASE | sed -e "s/.*\.//g")
    FN_NOEXT=$(basename $FN_BASE .${FN_EXT})

    # Download file
    WORKDIR=$(mktemp -d)
    IMG=$WORKDIR/$FN_BASE
    PTIFF=$WORKDIR/${FN_NOEXT}_pyramidal.tiff
    THUMB=$WORKDIR/${FN_NOEXT}_thumb.png
    gsutil cp "$SOURCE" "$IMG"

    # Already in pyramid format?
    if [[ $SKIP_PYRAMID ]]; then

        # Generate a thumbnail using openslide
        python os_thumb.py $IMG $THUMB $THUMBSIZE $THUMBSIZE

        # Upload the result
        gsutil cp $THUMB $TARGET/

    else

        # Perform conversion
        vips tiffsave $IMG $PTIFF \
            --vips-progress --compression=jpeg --Q=$JPEGLEVEL \
            --tile --tile-width=256 --tile-height=256 \
            --pyramid --bigtiff

        # Generate a thumbnail using openslide
        python os_thumb.py $PTIFF $THUMB $THUMBSIZE $THUMBSIZE

        # Upload the result
        gsutil cp $PTIFF $THUMB $TARGET/

    fi

    # Clean up
    rm -rf $WORKDIR
}

# Main entrypoint
CMD=${1:-usage}
shift 1
$CMD "$@"
