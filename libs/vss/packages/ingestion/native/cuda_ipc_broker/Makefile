DEEPSTREAM_ROOT ?= /opt/nvidia/deepstream/deepstream
CUDA_ROOT ?= /usr/local/cuda
CXX ?= g++
PKGS := gstreamer-1.0 gstreamer-app-1.0 gstreamer-video-1.0

CPPFLAGS += -I$(DEEPSTREAM_ROOT)/sources/includes -I$(CUDA_ROOT)/include
CXXFLAGS += -O2 -fPIC -std=c++17 -Wall -Wextra $(shell pkg-config --cflags $(PKGS))
LDLIBS += $(shell pkg-config --libs $(PKGS)) \
	-L$(DEEPSTREAM_ROOT)/lib -lnvbufsurface \
	-L$(CUDA_ROOT)/lib64 -lcudart
LDFLAGS += -shared -Wl,-rpath,$(DEEPSTREAM_ROOT)/lib -Wl,-rpath,$(CUDA_ROOT)/lib64

all: libvss_cuda_ipc_broker.so

libvss_cuda_ipc_broker.so: vss_cuda_ipc_broker.cpp vss_cuda_ipc_broker.h cuda_stream_sync.h surface_metadata.h
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) $(LDFLAGS) -o $@ vss_cuda_ipc_broker.cpp $(LDLIBS)

test_surface_metadata: test_surface_metadata.cpp surface_metadata.h
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) -o $@ test_surface_metadata.cpp

test_cuda_stream_sync: test_cuda_stream_sync.cpp cuda_stream_sync.h
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) -o $@ test_cuda_stream_sync.cpp \
		-L$(CUDA_ROOT)/lib64 -lcudart

test: test_surface_metadata test_cuda_stream_sync
	./test_surface_metadata
	./test_cuda_stream_sync

clean:
	rm -f libvss_cuda_ipc_broker.so test_surface_metadata test_cuda_stream_sync

.PHONY: all clean test
