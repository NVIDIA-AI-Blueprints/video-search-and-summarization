CC ?= gcc
PKGS := gstreamer-1.0 gstreamer-base-1.0
CFLAGS += -O2 -fPIC -Wall -Wextra -DPACKAGE=\"vssipcsrc\" \
	$(shell pkg-config --cflags $(PKGS))
LDLIBS += $(shell pkg-config --libs $(PKGS))

all: libgstvssipcsrc.so

libgstvssipcsrc.so: gstvssipcsrc.c
	$(CC) $(CFLAGS) -shared -o $@ $< $(LDLIBS)

clean:
	rm -f libgstvssipcsrc.so

.PHONY: all clean
