/*
 * SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

#include "utils.h"
#include "server.h"
#include "cmdline_parser.h"
#include "config.h"
#include "profiler.h"
#include <stdlib.h>
#include "vstmodule.h"

ModuleId getCurrentModuleId(const std::string& module_name)
{
    ModuleId moduleId = ModuleAll;
    ModuleLoader *moduleLoader = ModuleLoader::getInstance();
    if (moduleLoader != nullptr)
    {
        moduleId = moduleLoader->getModuleId(module_name);
    }

    if (moduleId == ModuleInvalid)
    {
        LOG(info) << "Invalid module specified modules are: sensor,rtspserver,recorder,storage,livestream,replaystream,streambridge" << endl;
        return moduleId;
    }
    if (moduleId == ModuleAll)
    {
        LOG(info) << "Loading all the vst modules" << endl;
        return moduleId;
    }
    LOG(info) << "Loading the vst module:" << module_name << endl;
    return moduleId;
}

int main(int argc, char *argv[])
{
    g_init_avaiable_memory = getAvailableMemory();
    CmdLineParser* cmdline_parser = CmdLineParser::getInstance();
    cmdline_parser->parseCommandLine (argc, argv);
    GET_CONFIG();
    {
        try
        {
            ModuleId module_id = ModuleAll;
            module_id = getCurrentModuleId(MODULE_ID);
            std::unique_ptr< VmsServer> Server(new VmsServer(module_id));
            Server->start();
        }
        catch(const std::exception& e)
        {
            LOG(error) << e.what() << '\n';
        }
    }

    LOG(info) << "Returing from Main...." << endl;
    DUMP_FUNCTION_PROFILER_RESULT
    return 0;
}

