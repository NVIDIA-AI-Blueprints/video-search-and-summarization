# nv-schema

nv protobuf schema

## protoc version

Use protobuf compiler `libprotoc 27.3` when regenerating nvschema outputs. Verify the local compiler version before running the generation commands below, because different `protoc` versions can produce incompatible or noisy generated-code diffs.

```bash
$ protoc --version
libprotoc 27.3
```

## generate c++

cd src/main

protoc -I=./protobuf/ --cpp_out=./c++/ ./protobuf/schema.proto
protoc -I=./protobuf/ --cpp_out=./c++/ ./protobuf/ext.proto

## generate javascript
cd src/main
protoc -I=protobuf --js_out=import_style=commonjs,binary:. protobuf/schema.proto
protoc -I=protobuf --js_out=import_style=commonjs,binary:. protobuf/ext.proto 

## generate java
cd src/main

protoc --java_out=. protobuf/schema.proto protobuf/ext.proto

## generate descriptor
cd src/main

protoc --descriptor_set_out=./schema.desc --include_imports protobuf/schema.proto
protoc --descriptor_set_out=./ext.desc --include_imports protobuf/ext.proto

## generate python

cd src/main

protoc -I=./protobuf/ --python_out=. --mypy_out=. protobuf/schema.proto
protoc -I=./protobuf/ --python_out=. --mypy_out=. protobuf/ext.proto

## generate ruby

Note: generating Ruby code requires protobuf compiler `libprotoc 3.20.3`.

```bash
$ protoc --version
libprotoc 3.20.3
```

cd src/main/
protoc --proto_path=protobuf/ --ruby_out=. protobuf/schema.proto protobuf/ext.proto
