#!/usr/bin/env python
# SPDX-FileCopyrightText: Copyright (c) 2022-2024, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import typing
from functools import partial
from typing import Generator

import numpy as np
import pandas as pd
import pytest

import cudf

from _utils import assert_results
from _utils.stages.conv_msg import ConvMsg
from morpheus.messages import ControlMessage
from morpheus.messages import MessageMeta
from morpheus.pipeline import LinearPipeline
from morpheus.pipeline.stage_decorator import stage
from morpheus.stages.general.monitor_stage import MonitorStage
from morpheus.stages.input.in_memory_data_generation_stage import InMemoryDataGenStage
from morpheus.stages.input.in_memory_source_stage import InMemorySourceStage
from morpheus.stages.output.compare_dataframe_stage import CompareDataFrameStage
from morpheus.stages.postprocess.add_classifications_stage import AddClassificationsStage
from morpheus.stages.postprocess.serialize_stage import SerializeStage
from morpheus.stages.preprocess.deserialize_stage import DeserializeStage


def build_expected(df: pd.DataFrame, threshold: float, class_labels: typing.List[str]):
    """
    Generate the expected output of an add class by filtering by a threshold and applying the class labels
    """
    df = (df > threshold)
    # Replace input columns with the class labels
    return df.rename(columns=dict(zip(df.columns, class_labels)))


def sample_message_meta_generator(df_rows: int, df_cols: int, count: int) -> Generator[MessageMeta, None, None]:
    data = {f'col_{i}': range(df_rows) for i in range(df_cols)}
    df = cudf.DataFrame(data)
    meta = MessageMeta(df)
    for _ in range(count):
        yield meta


@pytest.mark.use_cudf
@pytest.mark.usefixtures("use_cpp")
def test_monitor_stage_pipe(config):
    config.num_threads = 1

    df_rows = 10
    df_cols = 3
    expected_df = next(sample_message_meta_generator(df_rows, df_cols, 1)).copy_dataframe()

    count = 500

    cudf_generator = partial(sample_message_meta_generator, df_rows, df_cols, count)

    @stage
    def dummy_control_message_process_stage(msg: ControlMessage) -> ControlMessage:
        matrix_a = np.random.rand(3000, 3000)
        matrix_b = np.random.rand(3000, 3000)
        matrix_c = np.dot(matrix_a, matrix_b)
        msg.set_metadata("result", matrix_c[0][0])

        return msg

    pipe = LinearPipeline(config)
    pipe.set_source(InMemoryDataGenStage(config, cudf_generator, output_data_type=MessageMeta))
    pipe.add_stage(DeserializeStage(config, ensure_sliceable_index=True))
    pipe.add_stage(MonitorStage(config, description="preprocess", unit="pre process messages"))
    pipe.add_stage(dummy_control_message_process_stage(config))
    pipe.add_stage(MonitorStage(config, description="postprocess", unit="post process messages"))
    pipe.add_stage(SerializeStage(config))
    pipe.add_stage(MonitorStage(config, description="sink", unit="sink messages"))
    comp_stage = pipe.add_stage(CompareDataFrameStage(config, expected_df))
    pipe.run()

    assert_results(comp_stage.get_results())