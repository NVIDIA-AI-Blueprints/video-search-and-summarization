# VSS Training Notebooks

## Introduction

This folder contains a set of Jupyter Notebooks that will walk you through using VSS. 

- Lab 1 covers the basics of calling the VSS REST APIs to upload files, configure prompts and summarize videos. 
- Lab 2 will dive deeper into Graph RAG and the interactive Q&A feature of VSS. 

## Setup 

Start by cloning this repository and entering this examples directory. 

```
git clone https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization.git
cd video-search-and-summarization/examples/training_notebooks
```

### Pre-Requisites 

#### VSS Instance 
These training notebooks require access to a running VSS instance. The Jupyter Notebooks do not have to run on the same machine as VSS but it does need to be on the same network so it can access the VSS instance. 

If you do not have a VSS instance running, then first follow the [VSS documentation](https://docs.nvidia.com/vss/latest/index.html) to setup a VSS instance then come back and try the training notebooks. 

### Make and activate virtual environment (Optional)

It is recommended to setup a virtual environment to install the Python dependencies. Based on your OS, the commands are slightly different. For more resources on creating a virtual environments refer to the [Python documentation](https://docs.python.org/3/tutorial/venv.html). 

**Mac & Linux**
```
python3 -m venv venv 
source venv/bin/activate
```

**Windows**
```
python3 -m venv venv 
.\venv\Scripts\activate.bat
```

### Install dependencies

```
python3 -m pip install notebook 
```

## Launch Jupyter Notebook 

Once you have a running VSS instance and have setup your Python environment, then launch Jupyter Notebook. 

```
python3 -m notebook 
```

Once launched, follow the link in the terminal to access the Jupyter Notebook web UI then navigate to the lab 1 notebook to get started. 


