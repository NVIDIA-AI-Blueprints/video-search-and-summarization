/*
 * SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
 * SPDX-License-Identifier: LicenseRef-NvidiaProprietary
 *
 * NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
 * property and proprietary rights in and to this material, related
 * documentation and any modifications thereto. Any use, reproduction,
 * disclosure or distribution of this material and related documentation
 * without an express license agreement from NVIDIA CORPORATION or
 * its affiliates is strictly prohibited.
 */

import { Tab } from "./Tab";

export function SortableItem(props) {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition
  } = useSortable({ id: props.id });

  const backgroundColor =
    props.id == 1 ? "Orange" : props.id == 2 ? "pink" : "gray";

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    height: 100,
    width: 100,
    border: "2px solid black",
    backgroundColor,
    borderRadius: 10,
    touchAction: "none",
    margin: 20
  };

  return <Tab ref={setNodeRef} style={style} {...attributes} {...listeners} />;
}
