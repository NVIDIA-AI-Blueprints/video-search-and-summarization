// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: MIT
import React from 'react';
import {render} from '@testing-library/react';
import {CycleDetail, CycleSummary} from './CycleInspection';
import type {BottleCycle} from './types';
const cycle:BottleCycle={id:'fixture-cycle',label:'Bottle fixture',start_time:0,end_time:10,fill_start_time:1,fill_end_time:7,measurement_time:8,final_level:.65,status:'underfill',confidence:.8,reason:'Fixture model height below reviewed reference',evidence_start:6,evidence_end:10,reference_level:.75,tolerance:.05,overflow_time:null};
test('neural height attribution explicitly preserves the separate overflow method',()=>{
  const view=render(<CycleDetail cycle={cycle} currentLevel={.65} evidence={()=>undefined} neuralMeasurements/>);
  expect(view.getByText(/Final height and underfill are derived from RF-DETR/)).toBeTruthy();
  expect(view.getByText(/Overflow uses a separate exterior-color check/)).toBeTruthy();
  expect(view.queryByText(/RF-DETR masks are separate/)).toBeNull();
  view.rerender(<CycleDetail cycle={cycle} currentLevel={.65} evidence={()=>undefined}/>);
  expect(view.getByText(/Legacy calibrated final-height assessment/)).toBeTruthy();
});
test('missing neural height stays unreadable and does not become a fabricated zero',()=>{
  const view=render(<CycleDetail cycle={{...cycle,status:'uncertain',final_level:null,measurement_time:null}} currentLevel={null} evidence={()=>undefined} neuralMeasurements/>);
  expect(view.getAllByText('Unreadable')).toHaveLength(2);
  expect(view.getByText('Final height not established')).toBeTruthy();
  expect(view.queryByText('0.0%')).toBeNull();
});
test('inspection totals use returned cycles instead of expected video labels',()=>{
  const view=render(<CycleSummary cycles={[cycle,{...cycle,id:'unreadable',status:'uncertain',final_level:null}]} neuralMeasurements/>);
  expect(view.container.querySelector('.cycle-total strong')?.textContent).toBe('2');
  expect(view.container.querySelector('.cycle-underfill strong')?.textContent).toBe('1');
  expect(view.container.querySelector('.cycle-overflow strong')?.textContent).toBe('0');
  expect(view.container.querySelector('.cycle-uncertain strong')?.textContent).toBe('1');
});
