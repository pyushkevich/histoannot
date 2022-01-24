#!/bin/bash
MASTER_URL="http://picsl-histoannot-server"
DOCKER_TAG="latest"

# Name of the template
TEMPLATE_NAME="picsl-histoannot-worker-template-${DOCKER_TAG}"
GROUP_NAME="picsl-histoannot-worker-group-${DOCKER_TAG}"

# Service account info
SVCACCT=$(gcloud config get-value account)

function delete_template()
{
  if [[ $(gcloud compute instance-templates list \
    --format="value(name)" --filter="name=${TEMPLATE_NAME}" | wc -l) -gt 0 ]]; then
    gcloud --quiet compute instance-templates delete $TEMPLATE_NAME
  fi

}

function build_container()
{
  docker build -f docker/dzi_node/Dockerfile --build-arg SERVER_MODE=dzi_node -t dzi_node .
  docker tag dzi_node pyushkevich/histoannot-dzi-node:$DOCKER_TAG
  docker push pyushkevich/histoannot-dzi-node:$DOCKER_TAG
}

function create_template()
{
  # First delete existing
  delete_template

  # Build container
  build_container

  # Create template
  # -preemptible
  gcloud compute instance-templates create-with-container $TEMPLATE_NAME \
    --boot-disk-size=200G \
    --machine-type=n1-highcpu-4 \
    --preemptible \
    --tags=histoannot-worker \
    --subnet=histoannot-workers	\
    --service-account $SVCACCT \
    --container-image=docker.io/pyushkevich/histoannot-dzi-node:$DOCKER_TAG \
    --container-env=HISTOANNOT_URL_BASE=gs://mtl_histology,HISTOANNOT_SERVER_KEY=fsdfsdf,HISTOANNOT_MASTER_URL="$MASTER_URL"

}

function delete()
{
  # Delete group
  if [[ $(gcloud compute instance-groups list --format="value(name)" --filter="name=${GROUP_NAME}" | wc -l) -gt 0 ]]; then
    gcloud compute instance-groups managed delete ${GROUP_NAME}
  fi

  # Delete template
  delete_template
}

function create()
{
  # Clean existing
  delete

  # Create template
  create_template

  # Deploy group
  gcloud compute instance-groups managed create ${GROUP_NAME} \
    --base-instance-name ${GROUP_NAME} \
    --size 4 \
    --template ${TEMPLATE_NAME}
}

function update()
{
  # Build container
  build_container 

  # Get list of running instances
  nodes=$(gcloud compute instances list --format="value(name)" --filter="name~${GROUP_NAME}.*")

  # Update each node
  for node in $nodes; do
    gcloud compute instances update-container $node &
  done
}

CMD=${1?}
shift 1
$CMD "$@"
