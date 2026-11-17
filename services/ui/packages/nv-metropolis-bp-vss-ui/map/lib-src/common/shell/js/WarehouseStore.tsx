// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
import React, {
  createContext,
  useContext,
  useMemo,
  useRef,
  useSyncExternalStore,
} from 'react';

type Action = { type?: string; payload?: unknown };
type Reducer<S = unknown> = (state: S | undefined, action: Action) => S;
type Listener = () => void;

export type WarehouseStore = {
  getState: () => Record<string, unknown>;
  dispatch: (action: Action) => Action;
  subscribe: (listener: Listener) => () => void;
};

const StoreContext = createContext<WarehouseStore | null>(null);

export const combineReducers = (
  reducers: Record<string, Reducer>,
): Reducer<Record<string, unknown>> => {
  const keys = Object.keys(reducers);
  return (state, action) => {
    const prev = state || {};
    let changed = false;
    const next: Record<string, unknown> = {};
    for (const key of keys) {
      const slice = reducers[key](prev[key], action);
      next[key] = slice;
      if (slice !== prev[key]) {
        changed = true;
      }
    }
    return changed || !state ? next : prev;
  };
};

export const createStore = (
  reducer: Reducer<Record<string, unknown>>,
): WarehouseStore => {
  let state = reducer(undefined, { type: '@@INIT' });
  const listeners = new Set<Listener>();
  return {
    getState: () => state,
    dispatch: (action) => {
      state = reducer(state, action);
      listeners.forEach((listener) => listener());
      return action;
    },
    subscribe: (listener) => {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
  };
};

export const createSlice = ({
  name,
  initialState,
  reducers,
}: {
  name: string;
  initialState: unknown;
  reducers: Record<string, (state: any, action: Action) => unknown>;
}) => {
  const actions: Record<string, (payload?: unknown) => Action> = {};
  Object.keys(reducers).forEach((key) => {
    actions[key] = (payload) => ({ type: `${name}/${key}`, payload });
  });
  const reducer = (state = initialState, action: Action) => {
    if (!action?.type?.startsWith(`${name}/`)) {
      return state;
    }
    const key = action.type.slice(name.length + 1);
    const handler = reducers[key];
    return handler ? handler(state, action) : state;
  };
  return { name, reducer, actions };
};

const bindDispatch = (
  mapDispatch: unknown,
  dispatch: WarehouseStore['dispatch'],
) => {
  if (!mapDispatch) {
    return { dispatch };
  }
  if (typeof mapDispatch === 'function') {
    return (mapDispatch as (d: WarehouseStore['dispatch']) => object)(dispatch);
  }
  const creators = mapDispatch as Record<string, (...args: unknown[]) => Action>;
  const bound: Record<string, (...args: unknown[]) => Action> = {};
  Object.keys(creators).forEach((key) => {
    bound[key] = (...args) => dispatch(creators[key](...args));
  });
  return bound;
};

export const Provider = ({
  store,
  children,
}: {
  store: WarehouseStore;
  children?: React.ReactNode;
}) => (
  <StoreContext.Provider value={store}>{children}</StoreContext.Provider>
);

const EMPTY_MAPPED_PROPS = {};

const shallowEqual = (left: object, right: object) => {
  if (left === right) return true;
  const leftKeys = Object.keys(left);
  const rightKeys = Object.keys(right);
  if (leftKeys.length !== rightKeys.length) return false;
  return leftKeys.every((key) =>
    Object.is(
      (left as Record<string, unknown>)[key],
      (right as Record<string, unknown>)[key],
    ),
  );
};

const mappedProps = (
  store: WarehouseStore,
  mapStateToProps: ((state: Record<string, unknown>, ownProps?: object) => object) | null | undefined,
  ownProps: object,
  cached: { current: object | null },
) => {
  const next = mapStateToProps ? mapStateToProps(store.getState(), ownProps) : EMPTY_MAPPED_PROPS;
  if (cached.current && shallowEqual(cached.current, next)) {
    return cached.current;
  }
  cached.current = next;
  return next;
};

export const connect = (
  mapStateToProps?: ((state: Record<string, unknown>, ownProps?: object) => object) | null,
  mapDispatchToProps?: object | ((dispatch: WarehouseStore['dispatch']) => object) | null,
) =>
  (WrappedComponent: React.ComponentType<any>) => {
    const Connected = (props: object) => {
      const store = useContext(StoreContext);
      if (!store) {
        throw new Error('Warehouse store Provider is missing');
      }
      const ownPropsRef = useRef(props);
      ownPropsRef.current = props;
      const mappedRef = useRef<object | null>(null);
      const readMapped = () =>
        mappedProps(store, mapStateToProps, ownPropsRef.current, mappedRef);
      const stateProps = useSyncExternalStore(
        store.subscribe,
        readMapped,
        readMapped,
      );
      const dispatchProps = useMemo(
        () => bindDispatch(mapDispatchToProps, store.dispatch),
        [store],
      );
      return (
        <WrappedComponent {...props} {...stateProps} {...dispatchProps} />
      );
    };
    Connected.displayName = `Connect(${WrappedComponent.displayName || WrappedComponent.name || 'Component'})`;
    return Connected;
  };
