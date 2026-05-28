# nv-schema

nv protobuf schema

## generate c++

cd src/main/c++/

protoc -I=../protobuf/ --cpp_out=. ../protobuf/schema.proto

## generate javascript
cd src/main
protoc -I=protobuf --js_out=import_style=commonjs,binary:./javascript protobuf/schema.proto
protoc -I=protobuf --js_out=import_style=commonjs,binary:./javascript protobuf/ext.proto 

## generate java
cd src/main/protobuf/

protoc --java_out=../java schema.proto ext.proto

## generate descriptor
cd src/main/protobuf/

protoc --descriptor_set_out=../descriptors/schema.desc --include_imports schema.proto

## generate python

cd src/main/python/

protoc -I=../protobuf/ --python_out=. --mypy_out=. ../protobuf/schema.proto

## generate ruby
cd src/main/
protoc --proto_path=protobuf/ --ruby_out=ruby schema.proto ext.proto