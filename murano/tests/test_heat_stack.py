# Copyright (c) 2014 Hewlett-Packard Development Company, L.P.
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

import mock
from murano.tests import base

from murano.dsl import murano_object
from murano.engine.system import heat_stack


MOD_NAME = 'murano.engine.system.heat_stack'


class TestHeatStack(base.MuranoTestCase):
    def setUp(self):
        super(TestHeatStack, self).setUp()
        self.mock_murano_obj = mock.Mock(spec=murano_object.MuranoObject)
        self.mock_murano_obj.name = 'TestObj'
        self.mock_murano_obj.parents = []

    @mock.patch('heatclient.client.Client')
    def test_push_adds_version(self, mock_heat_client):
        """Assert that if heat_template_version is omitted, it's added"""
        # Note that the 'with x as y, a as b:' syntax was introduced in
        # python 2.7, and contextlib.nested was deprecated in py2.7
        with mock.patch(MOD_NAME + '.HeatStack._get_status') as status_get:
            with mock.patch(MOD_NAME + '.HeatStack._wait_state') as wait_st:

                status_get.return_value = 'NOT_FOUND'
                wait_st.return_value = {}

                hs = heat_stack.HeatStack(self.mock_murano_obj,
                                          None, None, None)
                hs._heat_client = mock_heat_client
                hs._name = 'test-stack'
                hs._template = {'resources': {'test': 1}}
                hs._parameters = {}
                hs._applied = False
                hs.push()

                expected_template = {
                    'heat_template_version': '2013-05-23',
                    'resources': {'test': 1}
                }
                mock_heat_client.stacks.create.assert_called_with(
                    stack_name='test-stack',
                    disable_rollback=False,
                    parameters={},
                    template=expected_template
                )
                self.assertTrue(hs._applied)

    def test_update_wrong_template_version(self):
        """Template version other than expected should cause error"""

        hs = heat_stack.HeatStack(self.mock_murano_obj,
                                  None, None, None)
        hs._name = 'test-stack'
        hs._template = {'resources': {'test': 1}}
        hs.type.properties = {}

        erroring_template = {
            'heat_template_version': 'something else'
        }

        with mock.patch(MOD_NAME + '.HeatStack.current') as current:
            current.return_value = {}

            e = self.assertRaises(heat_stack.HeatStackError,
                                  hs.updateTemplate,
                                  erroring_template)
            err_msg = "Currently only heat_template_version 2013-05-23 "\
                      "is supported."
            self.assertEqual(err_msg, str(e))

            # Check it's ok without a version
            hs.updateTemplate({})
            expected = {'resources': {'test': 1}}
            self.assertEqual(expected, hs._template)

            # .. or with a good version
            hs.updateTemplate({'heat_template_version': '2013-05-23'})
            expected['heat_template_version'] = '2013-05-23'
            self.assertEqual(expected, hs._template)
