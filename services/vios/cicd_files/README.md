<!--- Copyright (c) 2020, NVIDIA CORPORATION.  All rights reserved. --->

# Creating deployment image:

1. Go to CI/CD -> Piplelines
2. Click on "Run Pipeline"
3. Select the branch name
4. Enter variable name as "DEPLOY" and it's values as "1"
5. Enter variable name as "ARCH" and it's values as "x86_64" or "aarch64"
6. Enter another variable name as "IMAGE_TAG" and it's value as required.
7. If you need to push the container to NGC registry,
   then enter variable name PUSH_TO_NGC with 1 as a value and PROJECT variable
   with "vst" or "mms" or "nvstreamer" as value. Default is "vst".
8. Enter variable PUSH_TO_PROD with 1 to push container to prod registry, Default it will push to dev registry.
9. Enter variable name as "RELEASE" and it's values as "1"
10. Click on "Run Pipeline"
11. You should now see "deploy_project" running as a part of pipeline.

# Pushing Helm chart:
To push helm chart to NGC registery,
1. Enter variable name as a HELM_CHART & its value in tgz format (Eg. "vst-1.0.1.tgz").
2. Enter PROJECT variable with vms/vst/mms/nvstreamer as value.
3. Enter variable PUSH_TO_NGC with 1 as a value.
4. Enter variable PUSH_TO_PROD with 1 to push helm-chart to prod registry, Default it will push to dev registry.

# Gitlab runner details

* Go to Settings->CI/CD-> Runners
* Click on "New Project Runner"
* Select "Platform" as "Linux"
* You can add "tags" with required keywords if you need to setup "tagged" runner. Usually not needed, only needed if you need to run some specific jobs on your machine.
* Check "Run untagged job" if your runner should run "untagged" jobs. Usually, it should be checked for generic runner.
* Fill other fields as necessary.
* Make sure to check "Lock to current projects" so runner will be used for your project only.
* Click on "Create runner" which will redirect to a page containing your runner "TOKEN" which should be used while registering from your machine.

* Install git-lab runner binaries and register a runner on your gitlab-runner machine:

( Following instructions to install gitlab-runner might change: check official doc here: https://docs.gitlab.com/runner/install/linux-repository.html )

```
curl -L "https://packages.gitlab.com/install/repositories/runner/gitlab-runner/script.deb.sh" | sudo bash

sudo apt-get install gitlab-runner

sudo gitlab-runner register  \
	--url https://gitlab-master.nvidia.com  \
	--token <TOKEN>
```

If needed to run more concurrent jobs, change settings in "/etc/gitlab-runner/config.toml" and restart "gitlab-runner" as:

 `sudo systemctl restart gitlab-runner.service`


* Gitlab runners are running on following machines:

1. VM machine
    - server : hqnvumberlin150.nvidia.com
    - Username: svcumber
    - Password: fKny+w6$
    - gitlab-runners : 
        1. docker -  
        2. host - 

2. Dev Sanity machine
    - IP address : 10.24.217.28
    - Username : rbhagwat
    - passowrd : l4tmm
    - gitlab-runners
        1. host1

3. VST Dev machine
    - IP address : 10.24.140.196
    - Username : vst-dev
    - passowrd : nvidia
    - gitlab-runners
        1. docker
