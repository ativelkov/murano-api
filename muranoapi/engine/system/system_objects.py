# Copyright (c) 2013 Mirantis Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import inspect

import muranoapi.dsl.murano_class as murano_class
import muranoapi.engine.system.agent as agent
import muranoapi.engine.system.agent_listener as agent_listener
import muranoapi.engine.system.heat_stack as heat_stack
import muranoapi.engine.system.resource_manager as resource_manager


def _auto_register(class_loader):
    globs = globals().copy()
    for module_name, value in globs.iteritems():
        if inspect.ismodule(value):
            for class_name in dir(value):
                class_def = getattr(value, class_name)
                if inspect.isclass(class_def) and hasattr(
                        class_def, '_murano_class_name'):
                    class_loader.import_class(class_def)


def register(class_loader, path):
    _auto_register(class_loader)

    @murano_class.classname('io.murano.system.Resources')
    class ResourceManagerWrapper(resource_manager.ResourceManager):
        def initialize(self, _context, _class=None):
            super(ResourceManagerWrapper, self).initialize(
                path, _context, _class)

    class_loader.import_class(agent.Agent)
    class_loader.import_class(agent_listener.AgentListener)
    class_loader.import_class(heat_stack.HeatStack)
    class_loader.import_class(ResourceManagerWrapper)