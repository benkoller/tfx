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
"""Definition of Beam TFX runner."""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import datetime
import os
import re

import apache_beam as beam
import tensorflow as tf
from typing import Any, Iterable, List, Optional, Text

from tfx.components.base import base_component
from tfx.orchestration import component_launcher
from tfx.orchestration import data_types
from tfx.orchestration import pipeline
from tfx.orchestration import tfx_runner


# TODO(jyzhao): confirm it's re-executable, add test case.
@beam.typehints.with_input_types(Any)
@beam.typehints.with_output_types(Any)
class _ComponentAsDoFn(beam.DoFn):
  """Wrap component as beam DoFn."""

  def __init__(self, component: base_component.BaseComponent,
               tfx_pipeline: pipeline.Pipeline):
    """Initialize the _ComponentAsDoFn.

    Args:
      component: Component that to be executed.
      tfx_pipeline: Logical pipeline that contains pipeline related information.
    """
    driver_args = data_types.DriverArgs(enable_cache=tfx_pipeline.enable_cache)
    self._additional_pipeline_args = tfx_pipeline.additional_pipeline_args.copy()
    _job_name = re.sub(
      r'[^0-9a-zA-Z-]+',
      '-',
      '{pipeline_name}-{component}-{ts}'.format(
        pipeline_name=tfx_pipeline.pipeline_info.pipeline_name,
        component=component.component_id,
        ts=int(datetime.datetime.timestamp(datetime.datetime.now()))
      ).lower()
    )
    self._additional_pipeline_args['beam_pipeline_args'] = [
      arg for arg in self._additional_pipeline_args.setdefault(
        'beam_pipeline_args', []) if not arg.startswith("--job_name")
    ]
    self._additional_pipeline_args['beam_pipeline_args'].append(
          '--job_name={}'.format(_job_name))

    self._component_launcher = component_launcher.ComponentLauncher(
        component=component,
        pipeline_info=tfx_pipeline.pipeline_info,
        driver_args=driver_args,
        metadata_connection_config=tfx_pipeline.metadata_connection_config,
        additional_pipeline_args=self._additional_pipeline_args)
    self._component_id = component.component_id

  def process(self, element: Any, *signals: Iterable[Any]) -> None:
    """Executes component based on signals.

    Args:
      element: a signal element to trigger the component.
      *signals: side input signals indicate completeness of upstream components.
    """
    for signal in signals:
      assert not list(signal), 'Signal PCollection should be empty.'
    self._run_component()

  def _run_component(self) -> None:
    tf.logging.info('Component %s is running.', self._component_id)
    self._component_launcher.launch()
    tf.logging.info('Component %s is finished.', self._component_id)


class BeamDagRunner(tfx_runner.TfxRunner):
  """Tfx runner on Beam."""

  def __init__(self, beam_orchestrator_args: Optional[List[Text]] = None):
    """Initializes BeamDagRunner as a TFX orchestrator.

    Args:
      beam_orchestrator_args: beam args for the beam orchestrator. Note that
        this is different from the beam_pipeline_args within
        additional_pipeline_args, which is for beam pipelines in components.
    """
    super(BeamDagRunner, self).__init__()
    self._beam_orchestrator_args = beam_orchestrator_args or []

  def run(self, tfx_pipeline: pipeline.Pipeline) -> None:
    """Deploys given logical pipeline on Beam.

    Args:
      tfx_pipeline: Logical pipeline containing pipeline args and components.
    """
    # For CLI, while creating or updating pipeline, pipeline_args are extracted
    # and hence we avoid deploying the pipeline.

    # Append a beam orchestrator pipeline job name if none is present
    if not any(
      [arg.startswith("--job_name=") for arg in self._beam_orchestrator_args]):
      # Google Dataflow restricts naming to only alphanumeric and dashes
      orchestrator_job_name = re.sub(
        r'[^0-9a-zA-Z-]+',
        '-',
        '{pipeline_name}-{ts}'.format(
          pipeline_name=tfx_pipeline.pipeline_info.pipeline_name,
          ts=int(datetime.datetime.timestamp(datetime.datetime.now()))
        ).lower()
      )
      self._beam_orchestrator_args.append(
        '--job_name={}'.format(orchestrator_job_name))

    if 'TFX_JSON_EXPORT_PIPELINE_ARGS_PATH' in os.environ:
      return

    tfx_pipeline.pipeline_info.run_id = datetime.datetime.now().isoformat()

    with beam.Pipeline(argv=self._beam_orchestrator_args) as p:
      # Uses for triggering the component DoFns.
      root = p | 'CreateRoot' >> beam.Create([None])

      # Stores mapping of component to its signal.
      signal_map = {}
      # pipeline.components are in topological order.
      for component in tfx_pipeline.components:
        component_id = component.component_id

        # Signals from upstream components.
        signals_to_wait = []
        if component.upstream_nodes:
          for upstream_node in component.upstream_nodes:
            assert upstream_node in signal_map, ('Components is not in '
                                                 'topological order')
            signals_to_wait.append(signal_map[upstream_node])
        tf.logging.info('Component %s depends on %s.', component_id,
                        [s.producer.full_label for s in signals_to_wait])

        # Each signal is an empty PCollection. AsIter ensures component will be
        # triggered after upstream components are finished.
        signal_map[component] = (
            root
            | 'Run[%s]' % component_id >> beam.ParDo(
                _ComponentAsDoFn(component, tfx_pipeline),
                *[beam.pvalue.AsIter(s) for s in signals_to_wait]))
        tf.logging.info('Component %s is scheduled.', component_id)
