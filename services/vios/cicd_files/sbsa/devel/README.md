<!--- Copyright (c) 2020, NVIDIA CORPORATION.  All rights reserved. --->

# Creating cross-compilation image:

1. cd "cicd_files/jetson_cross_compilation"
2. Copy "tests_output.tbz2" L4T BSP package in current directory
3. Run "./build_cross_compile_container.sh"

# Updating target packages:

1. Modify "install_target_packages.sh" and create image using above steps

# Using container for build

1. Run
   docker run -it -v <source_tree>:/root <docker_image_name> bash -c "cd /root && make arm64 package -j8"
