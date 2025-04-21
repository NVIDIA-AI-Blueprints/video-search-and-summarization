# Custom VLM model

## Pre-requisites

### Fuyu8b

This is a locally exeucted model. Run the following command to download the model weights:
```sh
huggingface-cli download --resume-download --local-dir-use-symlinks False adept/fuyu-8b --local-dir fuyu8b
```

### Neva & Phi3v

These models are used as NVIDIA hosted services. Set ``NVIDIA_API_KEY`` env. variable to a valid API Key.

## Using a custom model

Set ``MODEL_PATH`` to the path of one of the model directory and ``VLM_MODEL_TO_USE`` to ``custom`` before starting the VIA application.
