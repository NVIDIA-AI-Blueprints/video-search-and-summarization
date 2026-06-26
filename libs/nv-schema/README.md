# nv-schema

nv protobuf schema

## protoc version

Use protobuf compiler `libprotoc 27.3` when regenerating nv-schema outputs. Verify the local compiler version before running the generation commands below, because different `protoc` versions can produce incompatible or noisy generated-code diffs.

```bash
$ protoc --version
libprotoc 27.3
```

Run the generation commands below from this directory (`libs/nv-schema/`), which contains the `protobuf/` sources.

> **Note on `struct.proto`:** only `schema.proto` and `ext.proto` are generated
> directly. `struct.proto` is a vendored copy of the well-known
> `google/protobuf/struct.proto` (package `google.protobuf`: `Struct`, `Value`,
> `ListValue`). It is not imported by the other schemas today — the
> `import "struct.proto"` in `ext.proto` is commented out — so there is no
> separate command for it. If it is re-enabled, `protoc` pulls it in as a
> dependency of `ext.proto`; it is never generated on its own.

## generate c++

```bash
protoc -I=./protobuf/ --cpp_out=. ./protobuf/schema.proto
protoc -I=./protobuf/ --cpp_out=. ./protobuf/ext.proto
```

## generate javascript

```bash
protoc -I=protobuf --js_out=import_style=commonjs,binary:. protobuf/schema.proto
protoc -I=protobuf --js_out=import_style=commonjs,binary:. protobuf/ext.proto
```

## generate java

```bash
protoc --java_out=. protobuf/schema.proto protobuf/ext.proto
```

## generate descriptor

```bash
protoc --descriptor_set_out=./schema.desc --include_imports protobuf/schema.proto
protoc --descriptor_set_out=./ext.desc --include_imports protobuf/ext.proto
```

## generate python

```bash
protoc -I=./protobuf/ --python_out=. --mypy_out=. protobuf/schema.proto
protoc -I=./protobuf/ --python_out=. --mypy_out=. protobuf/ext.proto
```

## generate ruby

Note: generating Ruby code requires protobuf compiler `libprotoc 3.20.3`.

```bash
$ protoc --version
libprotoc 3.20.3
```

```bash
protoc --proto_path=protobuf/ --ruby_out=. protobuf/schema.proto protobuf/ext.proto
```
