# Copyright 2019 Google LLC. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Tests for tfx.orchestration.beam.beam_dag_runner."""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import mock
import tensorflow as tf
from ml_metadata.proto import metadata_store_pb2
from tfx import types
from tfx.components.base import base_component
from tfx.components.base import base_executor
from tfx.components.base import executor_spec
from tfx.orchestration import pipeline
from tfx.orchestration.beam import beam_dag_runner
from tfx.types.component_spec import ChannelParameter

_executed_components = []


class _FakeComponentAsDoFn(beam_dag_runner._ComponentAsDoFn):

  def _run_component(self):
    _executed_components.append(self._component_id)


# We define fake component spec classes below for testing. Note that we can't
# programmatically generate component using anonymous classes for testing
# because of a limitation in the "dill" pickler component used by Apache Beam.
# An alternative we considered but rejected here was to write a function that
# returns anonymous classes within that function's closure (as is done in
# tfx/orchestration/pipeline_test.py), but that strategy does not work here
# as these anonymous classes cannot be used with Beam, since they cannot be
# pickled with the "dill" library.
class _FakeComponentSpecA(types.ComponentSpec):
  PARAMETERS = {}
  INPUTS = {}
  OUTPUTS = {'output': ChannelParameter(type_name='a')}


class _FakeComponentSpecB(types.ComponentSpec):
  PARAMETERS = {}
  INPUTS = {'a': ChannelParameter(type_name='a')}
  OUTPUTS = {'output': ChannelParameter(type_name='b')}


class _FakeComponentSpecC(types.ComponentSpec):
  PARAMETERS = {}
  INPUTS = {'a': ChannelParameter(type_name='a')}
  OUTPUTS = {'output': ChannelParameter(type_name='c')}


class _FakeComponentSpecD(types.ComponentSpec):
  PARAMETERS = {}
  INPUTS = {
      'b': ChannelParameter(type_name='b'),
      'c': ChannelParameter(type_name='c'),
  }
  OUTPUTS = {'output': ChannelParameter(type_name='d')}


class _FakeComponentSpecE(types.ComponentSpec):
  PARAMETERS = {}
  INPUTS = {
      'a': ChannelParameter(type_name='a'),
      'b': ChannelParameter(type_name='b'),
      'd': ChannelParameter(type_name='d'),
  }
  OUTPUTS = {'output': ChannelParameter(type_name='e')}


class _FakeComponent(base_component.BaseComponent):

  SPEC_CLASS = types.ComponentSpec
  EXECUTOR_SPEC = executor_spec.ExecutorClassSpec(base_executor.BaseExecutor)

  def __init__(self, spec: types.ComponentSpec):
    instance_name = spec.__class__.__name__.replace(
        '_FakeComponentSpec', '').lower()
    super(_FakeComponent, self).__init__(spec=spec,
                                         instance_name=instance_name)


class BeamDagRunnerTest(tf.test.TestCase):

  @mock.patch.multiple(
      beam_dag_runner,
      _ComponentAsDoFn=_FakeComponentAsDoFn,
  )
  def testRun(self):
    component_a = _FakeComponent(
        _FakeComponentSpecA(output=types.Channel(type_name='a')))
    component_b = _FakeComponent(
        _FakeComponentSpecB(
            a=component_a.outputs.output, output=types.Channel(type_name='b')))
    component_c = _FakeComponent(
        _FakeComponentSpecC(
            a=component_a.outputs.output, output=types.Channel(type_name='c')))
    component_d = _FakeComponent(
        _FakeComponentSpecD(
            b=component_b.outputs.output,
            c=component_c.outputs.output,
            output=types.Channel(type_name='d')))
    component_e = _FakeComponent(
        _FakeComponentSpecE(
            a=component_a.outputs.output,
            b=component_b.outputs.output,
            d=component_d.outputs.output,
            output=types.Channel(type_name='e')))

    test_pipeline = pipeline.Pipeline(
        pipeline_name='x',
        pipeline_root='y',
        metadata_connection_config=metadata_store_pb2.ConnectionConfig(),
        components=[
            component_d, component_c, component_a, component_b, component_e
        ])

    beam_dag_runner.BeamDagRunner().run(test_pipeline)
    self.assertItemsEqual(_executed_components, [
        '_FakeComponent.a', '_FakeComponent.b', '_FakeComponent.c',
        '_FakeComponent.d', '_FakeComponent.e'
    ])
    self.assertEqual(_executed_components[0], '_FakeComponent.a')
    self.assertEqual(_executed_components[3], '_FakeComponent.d')
    self.assertEqual(_executed_components[4], '_FakeComponent.e')


if __name__ == '__main__':
  tf.test.main()
