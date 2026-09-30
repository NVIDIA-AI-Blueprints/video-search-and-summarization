# Camera Onboarding Tool

This web app was created to improve the process of onboarding sensors to ITS. The app contains tools to perfom:

- Camera calibration tool to convert pixels to global coordinates
- Validation tool to test the sensor calibration results
- Road links drawing tool to assist with road link generation

## Production: Installation and Usage

The first step is to setup the django server in python (version at least 3.6 requred). First install setup your python environment:

```bash
cd server
pip install -r requirements.txt
```

Change to one directory to one directory above the calibration repo (i.e. the directory that the calibration repo was cloned into). Then run:

```
manage.py collectstatic
gunicorn --timeout 1000 calibration.server.server.wsgi
```

The full app should be available at `http://127.0.0.1:8000`

## Development: Installation and Usage

### Backend

The first step is to setup the django server in python (version at least 3.6 requred). First install setup your python environment:

```bash
cd server
pip install -r requirements.txt
```

With django and the required packages installed, open a terminal to the server directory and run:

```bash
python manage.py runserver
```

The server should start in `http://127.0.0.1:8000/`.

### Frontend

The second step is to start the react frontend. To do so, open a terminal and run:

```bash
cd client
npm install
```

Setup frontend environment.

```bash
npm start
```

#### Frontend Environment Setup

It is also required to setup environment variables in the `client` folder.

- `.env.development` is for the development environment. When you run `npm start`, all `process.env` in js files will be injected according to their names. Chaning any environment variables will require a restart of the development server if it is running.
- `.env.production` is not yet implemented.
- The following environment variables are required to run the project.

```json
REACT_APP_API_ENDPOINT_BASE_URL = '<URL>'
REACT_APP_GOOGLE_MAPS_API_KEY = '<KEY>'
```

Example:

```json
REACT_APP_API_ENDPOINT_BASE_URL = 'http://127.0.0.1:8000/api'
REACT_APP_GOOGLE_MAPS_API_KEY = 'someAPIkey'
```

The client should start in `http://localhost:3000/`. Open this in a web browser to use the react sensor onboarding tool.

### Additional Requirements for Road Links Validation Only

To generate and validate the road links, it is also required to install java and apache spark.

#### Instaling Java

To install java, run

```
sudo apt-get update
sudo apt-get install openjdk-8-jdk
```

To verify the installation, run

```
java -version
```

#### Installing Apache Spark

To install apache spark, download version 2.4.5 [here](https://spark.apache.org/downloads.html).
Once downloaded, change the third argument in the args variable in `roadSegment.py` to be:

```
"/PATH_TO_YOUR_SPARK_DIRECTORY/spark-2.4.5-bin-hadoop2.7/jars/*:resources/stream-ITS-1.0-jar-with-dependencies.jar"
```

## Tool descriptions

### Camera Calibration Tool

### Calibration Validation Tool

### Road Links Drawing Tool
