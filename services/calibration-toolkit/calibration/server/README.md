
docker build -t client:latest client
docker run -v $PWD/client:/app/client -p 3000:3000 client:latest npm start


docker build -t server:latest server
docker run -v $PWD/server:/app/server -p 8000:8000 server:latest

