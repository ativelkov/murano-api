#    Copyright (c) 2014 Mirantis, Inc.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import collections
import functools
import inspect
import types
import uuid

import eventlet
import eventlet.event
import yaql.context

import muranoapi.dsl.attribute_store as attribute_store
import muranoapi.dsl.exceptions as exceptions
import muranoapi.dsl.expressions as expressions
import muranoapi.dsl.helpers as helpers
import muranoapi.dsl.murano_object as murano_object
import muranoapi.dsl.object_store as object_store
import muranoapi.dsl.yaql_functions as yaql_functions

from muranoapi.openstack.common import log as logging

LOG = logging.getLogger(__name__)


class MuranoDslExecutor(object):
    def __init__(self, class_loader, environment=None):
        self._class_loader = class_loader
        self._object_store = object_store.ObjectStore(class_loader)
        self._attribute_store = attribute_store.AttributeStore()
        self._root_context = class_loader.create_root_context()
        self._root_context.set_data(self, '?executor')
        self._root_context.set_data(self._class_loader, '?classLoader')
        self._root_context.set_data(environment, '?environment')
        self._root_context.set_data(self._object_store, '?objectStore')
        self._root_context.set_data(self._attribute_store, '?attributeStore')
        self._locks = {}
        yaql_functions.register(self._root_context)
        self._root_context = yaql.context.Context(self._root_context)

    @property
    def object_store(self):
        return self._object_store

    @property
    def attribute_store(self):
        return self._attribute_store

    def to_yaql_args(self, args):
        if not args:
            return tuple()
        elif isinstance(args, types.TupleType):
            return args
        elif isinstance(args, types.ListType):
            return tuple(args)
        elif isinstance(args, types.DictionaryType):
            return tuple(args.items())
        else:
            raise ValueError()

    def invoke_method(self, name, this, context, murano_class, *args):
        if context is None:
            context = self._root_context
        implementations = this.type.find_method(name)
        # restore this from upcast object (no change if there was no upcast)
        this = this.real_this
        delegates = []
        for declaring_class, name in implementations:
            method = declaring_class.get_method(name)
            if not method:
                continue
            arguments_scheme = method.arguments_scheme
            try:
                params = self._evaluate_parameters(
                    arguments_scheme, context, this, *args)
                delegates.append(functools.partial(
                    self._invoke_method_implementation,
                    method, this, declaring_class, context, params))
            except TypeError:
                continue
        if len(delegates) < 1:
            raise exceptions.NoMethodFound(name)
        elif len(delegates) > 1:
            raise exceptions.AmbiguousMethodName(name)
        else:
            return delegates[0]()

    def _invoke_method_implementation(self, method, this, murano_class,
                                      context, params):
        body = method.body
        if not body:
            return None

        current_thread = eventlet.greenthread.getcurrent()
        if not hasattr(current_thread, '_murano_dsl_thread_marker'):
            thread_marker = current_thread._murano_dsl_thread_marker = \
                uuid.uuid4().hex
        else:
            thread_marker = current_thread._murano_dsl_thread_marker

        method_id = id(body)
        this_id = this.object_id

        while True:
            event, marker = self._locks.get((method_id, this_id), (None, None))
            if event:
                if marker == thread_marker:
                    return self._invoke_method_implementation_gt(
                        body, this, params, murano_class, context)
                event.wait()
            else:
                break

        event = eventlet.event.Event()
        self._locks[(method_id, this_id)] = (event, thread_marker)
        # noinspection PyProtectedMember
        method_info = "{0}.{1} ({2})".format(murano_class.name, method._name,
                                             hash((method_id, this_id)))
        LOG.debug(
            "{0}: Begin execution: {1}".format(thread_marker, method_info))
        gt = eventlet.spawn(self._invoke_method_implementation_gt, body,
                            this, params, murano_class, context,
                            thread_marker)
        result = gt.wait()
        del self._locks[(method_id, this_id)]
        LOG.debug(
            "{0}: End execution: {1}".format(thread_marker, method_info))
        event.send()
        return result

    def _invoke_method_implementation_gt(self, body, this,
                                         params, murano_class, context,
                                         thread_marker=None):
        if thread_marker:
            current_thread = eventlet.greenthread.getcurrent()
            current_thread._murano_dsl_thread_marker = thread_marker
        if callable(body):
            if '_context' in inspect.getargspec(body).args:
                params['_context'] = self._create_context(
                    this, murano_class, context, **params)
            if inspect.ismethod(body) and not body.__self__:
                return body(this, **params)
            else:
                return body(**params)
        elif isinstance(body, expressions.DslExpression):
            return self.execute(body, murano_class, this, context, **params)
        else:
            raise ValueError()

    def _evaluate_parameters(self, arguments_scheme, context, this, *args):
        arg_names = list(arguments_scheme.keys())
        parameter_values = {}
        i = 0
        for arg in args:
            value = helpers.evaluate(arg, context)
            if isinstance(value, types.TupleType) and len(value) == 2 and \
                    isinstance(value[0], types.StringTypes):
                name = value[0]
                value = value[1]
                if name not in arguments_scheme:
                    raise TypeError()
            else:
                if i >= len(arg_names):
                    raise TypeError()
                name = arg_names[i]
                i += 1

            if callable(value):
                value = value()
            arg_spec = arguments_scheme[name]
            parameter_values[name] = arg_spec.validate(
                value, this, self._root_context, self._object_store)

        for name, arg_spec in arguments_scheme.iteritems():
            if name not in parameter_values:
                if not arg_spec.has_default:
                    raise TypeError()
                parameter_context = self._create_context(
                    this, this.type, context)
                parameter_values[name] = arg_spec.validate(
                    helpers.evaluate(arg_spec.default, parameter_context),
                    this, self._root_context, self._object_store)

        return parameter_values

    def _create_context(self, this, murano_class, context, **kwargs):
        new_context = self._class_loader.create_local_context(
            parent_context=self._root_context,
            murano_class=murano_class)
        new_context.set_data(this)
        new_context.set_data(this, 'this')
        new_context.set_data(this, '?this')
        new_context.set_data(murano_class, '?type')
        new_context.set_data(context, '?callerContext')

        @yaql.context.EvalArg('obj', arg_type=murano_object.MuranoObject)
        @yaql.context.EvalArg('property_name', arg_type=str)
        def obj_attribution(obj, property_name):
            return obj.get_property(property_name, murano_class)

        @yaql.context.EvalArg('prefix', str)
        @yaql.context.EvalArg('name', str)
        def validate(prefix, name):
            return murano_class.namespace_resolver.resolve_name(
                '%s:%s' % (prefix, name))

        new_context.register_function(obj_attribution, '#operator_.')
        new_context.register_function(validate, '#validate')
        for key, value in kwargs.iteritems():
            new_context.set_data(value, key)
        return new_context

    def execute(self, expression, murano_class, this, context, **kwargs):
        new_context = self._create_context(
            this, murano_class, context, **kwargs)
        return expression.execute(new_context, murano_class)

    def load(self, data):
        if not isinstance(data, types.DictionaryType):
            raise TypeError()
        self._attribute_store.load(data.get('Attributes') or [])
        result = self._object_store.load(data.get('Objects') or {},
                                         None, self._root_context)
        self.cleanup(data)
        return result

    def cleanup(self, data):
        objects_copy = data.get('ObjectsCopy')
        if not objects_copy:
            return
        gc_object_store = object_store.ObjectStore(self._class_loader)
        gc_object_store.load(objects_copy, None, self._root_context)
        objects_to_clean = []
        for object_id in self._list_potential_object_ids(objects_copy):
            if gc_object_store.has(object_id) \
                    and not self._object_store.has(object_id):
                obj = gc_object_store.get(object_id)
                objects_to_clean.append(obj)
        if objects_to_clean:
            backup = self._object_store
            try:
                self._object_store = gc_object_store
                for obj in objects_to_clean:
                    methods = obj.type.find_method('destroy')
                    for cls, method in methods:
                        try:
                            cls.invoke(method, self, obj, {})
                        except Exception:
                            pass
            finally:
                self._object_store = backup

    def _list_potential_object_ids(self, data):
        if isinstance(data, types.DictionaryType):
            for val in data.values():
                for res in self._list_potential_object_ids(val):
                    yield res
            sys_dict = data.get('?')
            if isinstance(sys_dict, types.DictionaryType) \
                    and sys_dict.get('id') \
                    and sys_dict.get('type'):
                yield sys_dict['id']
        elif isinstance(data, collections.Iterable) and not isinstance(
                data, types.StringTypes):
            for val in data:
                for res in self._list_potential_object_ids(val):
                    yield res
