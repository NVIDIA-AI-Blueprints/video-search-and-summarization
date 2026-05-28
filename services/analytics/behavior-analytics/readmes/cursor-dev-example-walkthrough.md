# Cursor Dev Example Walkthrough

> Part of behavior-analytics docs. See `../README.md` for the project overview and `readmes/building-mdx-analytics-app.md` for the how-to guide. For module map, see `readmes/modules-overview.md`.

## Overview

This guide documents a real Cursor AI session where we iteratively developed a new analytics application with custom incident detection. It demonstrates how to effectively communicate requirements, handle errors, and refine code through multiple iterations.

### TL;DR — Working with Cursor
- Start with clear requirements and concrete refs/scope: point to files to mirror and what to include/exclude.
- Better to have a good understanding of the framework’s patterns before giving refactor feedback.
- Iterate tightly: propose, inspect, correct; keep feedback specific and anchored to the requirements.
- Co-create unit tests with Cursor, run them, and fix bugs/issues.
- Always manually review the code and verify behavior before calling it done.

## The Journey: From Initial Request to Production Code

### Phase 1: Initial Code Generation

**User Request:**
```
“Using `apps/analytics/main_analytics_2d_app.py` and `apps/analytics/main_analytics_3d_app.py` as references, build a new app named `main_dev_example_app.py` that only implements the FOV-based incident.”
```

**Cursor's Approach:**
- Analyzed two existing apps (warehouse_2d and warehouse_3d)
- Created a simplified version focusing on FOV incidents
- Generated initial implementation with custom FOV logic

### Phase 2: Requirements Refinement

**User Clarification:**
```
"I don't need all the previous incidents. Instead I need:
- ROI restricted violation incidents
- A new type of incident based on FOV count check—alert when a specified 
  number of objects are present in the field of view"
```

**Key Learning:** Be specific about what to include AND exclude. The user clarified they wanted ROI violations plus a NEW type of FOV-based incident (count-based), not the existing FOV incidents.

After Phase 2, Cursor produced a reasonable solution but placed all logic in the main app. It should work after debugging and testing.

> Note — Who should continue past Phase 2  
> - Developers without repository access (only main app code): Phase 1 and Phase 2 give you a workable baseline; provide high-level guidance but avoid deep refactors.  
> - Developers with repository access: Continue through Phase 3+ and iterate with Cursor to improve architecture/logic. Review `readmes/building-mdx-analytics-app.md` and `readmes/modules-overview.md` for deeper context before suggesting changes.

### Phase 3: Architecture Integration

**User Guidance:**
```
"Use frame_state_management.py as the reference for incident generation and move the logic there instead of keeping it in the main app."
```

**Cursor's Response:**
- Studied the existing incident generation patterns
- Initially created an extended class
- User corrected: "Put the logic inside frame_state_management.py directly"

**Key Learning:** When extending existing systems, decide early whether to extend classes or modify core implementations.

### Phase 4: Critical Implementation Decisions

#### 1. Naming Consistency

**Evolution of Naming:**
- First: "FOV Violation"
- User note: rename to “FOV Count Violation” for clarity.
- Final pattern: `fov_count_violation_incident_*` for all configs

**Lesson:** Consistent naming across configs, methods, and states is crucial for maintainability.

#### 2. Handling Edge Cases

**Primary Object ID Challenge:**
```python
# User note: no primary object ID for this incident
# Solution: make primary_object_id Optional[str]
# For FOV count violations: violation_id = sensor_id (not sensor_id + primary_object_id)
```

**Lesson:** Aggregate violations (like count-based) don't have a primary object, unlike object-specific violations.

#### 3. State Management Simplification

**Evolution:**
```python
# Initial: Dict[str, IncidentState] (following other patterns)
# User insight: since violation_id is only sensor_id now, we don't need a dict
# Final: Optional[IncidentState] (single state per sensor)
```

**Lesson:** Don't blindly follow patterns. Simplify when the use case allows.

### Phase 5: Error Resolution Patterns

#### Linter Errors and Type Safety

**Problem:** Multiple linter errors about accessing attributes on Union types

**User's Pragmatic Approach:** Skip casts/asserts and focus on working code; linter issues were deprioritized.

**Lesson:** Sometimes shipping working code is more important than satisfying every linter rule. Focus on functionality first.

#### Test Failures and Fixes

**Issue:** Tests expecting 4 incidents but getting 3

**Resolution Process:**
1. Initial fix: Increase duration to 3.5 seconds
2. User feedback: "3 second should work?"
3. Better fix: Align all thresholds to 2 seconds for clarity

**Lesson:** Make tests predictable and easy to understand. Align test data with clear expectations.

### Phase 6: Configuration Management

#### Creating Appropriate Config Files

**User Request:** “This is the config for 2d; can you help create a config for the new app?”

**Key Decisions:**
- Removed unused topics (frames, behavior, events)
- Added only "incidents" topic
- Enabled only relevant incident types
- Set appropriate thresholds for demo purposes

### Phase 7: Best Practices Learned

#### 1. Iterative Development
- Start with a working example
- Modify incrementally
- Test assumptions early
- Don't over-engineer initially

#### 2. Clear Communication
- Reference specific files and line numbers
- Explain the "why" behind changes
- Correct misunderstandings immediately

#### 3. Pragmatic Decisions
- Balance code quality with functionality
- Simplify when possible
- Don't blindly follow patterns
- Focus on the specific use case

#### 4. Testing Strategy
- Write comprehensive tests for new features
- Ensure test data aligns with configured thresholds
- Test edge cases (empty lists, None values)
- Verify integration with existing systems

## Code Generation Workflow

### Effective Request Pattern
```
1. Reference existing code: @file1 @file2
2. Specify what to create: "new app named X"
3. Define scope: "only need Y feature"
4. Iterate on details: "change this specific part"
5. Fix issues pragmatically: "ignore linter, focus on function"
```

### Common Refinement Requests
- "Put it under a new folder"
- "Rename X to Y"
- "Make this parameter optional"
- "Simplify this structure"
- "No need for this check"

## Key Technical Insights

### FOV Count Violation Implementation
1. **No Primary Object**: Aggregate violations track sensor-level states
2. **Single State**: One violation state per sensor (not a dictionary)
3. **Config Pattern**: FOV_COUNT_VIOLATION_INCIDENT_* naming

### Integration Points
- Enhanced frames with FOV metrics
- Reused `_process_violation_state` for consistency
- Extended `get_incidents()` to include new type
- Added comprehensive unit tests

## Results

### What We Built
- ✅ Clean, simplified app using existing FrameStateMgmt
- ✅ New FOV count violation type integrated into core
- ✅ Comprehensive unit tests (12 new test cases)
- ✅ Production-ready configuration file
- ✅ Consistent with existing architecture

### Lines of Code
- Main app: 89 lines (simplified from 141)
- Core changes: ~100 lines added to frame_state_management.py
- Tests: 300+ lines of comprehensive test coverage
- Config: 88 lines of clean JSON configuration

## Conclusion

Effective code generation with Cursor is about:
1. **Clear communication** - Be specific about requirements
2. **Iterative refinement** - Don't expect perfection on first try
3. **Pragmatic decisions** - Balance ideal vs. practical
4. **Learning from patterns** - But knowing when to deviate
5. **Testing thoroughly** - Ensure new code integrates properly

The key is treating Cursor as a collaborative partner who needs:
- Context about your system
- Clear requirements and constraints
- Feedback on what's working or not
- Guidance on architectural decisions

---

*This guide documents an actual Cursor session where we added a new incident type to an existing analytics system, demonstrating real-world iteration and problem-solving.*