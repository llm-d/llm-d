#!/bin/bash

# requires: git, pip

GUIDELLM_REMOTE=${GUIDELLM_REMOTE:-"https://github.com/tlrmchlsmth/guidellm.git"}
GUIDELLM_BRANCH=${GUIDELLM_BRANCH:-"max_completion_tokens"}

git clone ${GUIDELLM_REMOTE}
cd guidellm
git checkout ${GUIDELLM_BRANCH}
pip install -e .
