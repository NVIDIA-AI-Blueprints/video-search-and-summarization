// SPDX-License-Identifier: MIT
/**
 * Intermediate-step tree assembly.
 *
 * Steps arrive out of order and are revised in place: a step first appears
 * `in_progress` and is later resent `complete`. The tree is built by id and
 * `parent_id`, and a resent step replaces its earlier form rather than
 * appearing twice.
 */
import type { IntermediateStep } from '../types/websocket';

/**
 * Replaces a step already in the tree. Matches on id *and* step name, since a
 * backend may reuse an id across differently-named steps of one run.
 *
 * Mutates in place and returns whether a match was found.
 */
function replaceStep(steps: IntermediateStep[], incoming: IntermediateStep): boolean {
  for (let i = 0; i < steps.length; i += 1) {
    if (steps[i].id === incoming.id && steps[i].content?.name === incoming.content?.name) {
      // The index is the render position and belongs to the slot, not the payload.
      steps[i] = { ...incoming, index: steps[i].index };
      return true;
    }

    const children = steps[i].intermediate_steps;
    if (children && children.length > 0 && replaceStep(children, incoming)) {
      return true;
    }
  }

  return false;
}

/** Depth-first search for the step a child should nest under. */
function findStepById(steps: IntermediateStep[], id: string): IntermediateStep | null {
  for (const step of steps) {
    if (step.id === id) return step;

    const children = step.intermediate_steps;
    if (children && children.length > 0) {
      const found = findStepById(children, id);
      if (found) return found;
    }
  }

  return null;
}

/**
 * Folds one step into the tree.
 *
 * A step with no id cannot be addressed or revised, so it is ignored. When
 * `override` is set, a matching step is replaced; otherwise the step is nested
 * under its parent, or appended at the root when the parent has not arrived.
 *
 * Returns the same array instance it was given — callers treat the tree as
 * owned by the message being built.
 */
export function processIntermediateMessage(
  existingSteps: IntermediateStep[] = [],
  newMessage: IntermediateStep = {} as IntermediateStep,
  override = true,
): IntermediateStep[] {
  if (!newMessage.id) return existingSteps;

  try {
    if (override && replaceStep(existingSteps, newMessage)) {
      return existingSteps;
    }

    if (newMessage.parent_id) {
      const parent = findStepById(existingSteps, newMessage.parent_id);
      if (parent) {
        if (!parent.intermediate_steps) parent.intermediate_steps = [];
        parent.intermediate_steps.push(newMessage);
        return existingSteps;
      }
    }

    existingSteps.push(newMessage);
    return existingSteps;
  } catch {
    // A malformed step must not lose the steps already rendered.
    return existingSteps;
  }
}
