# Barnaby Premier Tier Design

**Date:** 2025-09-27
**Status:** Draft
**Approach:** Developer Experience Overhaul First (Approach 2)

## Overview

Transform Barnaby from a solid voice coding interface into a premier-tier developer productivity platform by focusing on immediate value for existing Windows users. This design prioritizes advanced voice features, deep IDE integrations, workflow automation, and enhanced UI polish while deferring cross-platform expansion to a later phase.

## Scope

**Primary focus areas:**
1. Advanced voice features (wake word + continuous dictation)
2. Full-featured IDE extensions (VS Code + Cursor + Claude Desktop)
3. Workflow automation (custom commands, macros, templates)
4. Enhanced UI (themes, layouts, keyboard navigation)

**Explicitly out of scope for this phase:**
- Cross-platform support (macOS, Linux)
- Analytics dashboard (deferred)
- Distribution improvements (deferred)
- Enterprise features (deferred)

## Section 1: Advanced Voice Features

### Architecture

Add lightweight wake word detection and continuous dictation mode to complement the existing Whisper-based transcription system.

### Key Components

- `engine/barnaby_engine/wake_word.py` - Wake word detection with configurable keyword
- `engine/barnaby_engine/continuous.py` - Continuous dictation mode with smart pause detection
- `engine/barnaby_engine/command_chain.py` - Command chaining and context retention
- New UI controls in Sidebar for mode switching and wake word status

### Features

**Wake Word Detection:**
- Always-on low-CPU detector running alongside Whisper
- Configurable wake word (default "Barnaby")
- Sensitivity settings for different environments
- Visual indicator when wake word is active

**Continuous Dictation Mode:**
- Toggle to keep microphone open between turns
- Smart voice activity detection (VAD) to distinguish pauses from completion
- Configurable timeout (how long to wait before considering turn complete)
- Visual feedback showing listening state

**Command Chaining:**
- Execute multiple voice instructions without re-activating
- Context retention across chained commands
- Chain depth limits to prevent runaway sequences
- Clear visual indication of active chain

### Data Flow

1. Wake word detector runs continuously (low CPU, always-on)
2. When detected, activates full Whisper transcription
3. In continuous mode, VAD determines when user finishes a turn
4. Command chaining maintains context across sequential instructions
5. User can interrupt or stop with voice commands or UI controls

### Configuration

- Wake word sensitivity and custom keywords
- Continuous mode timeout (default: 2 seconds of silence)
- Command chaining depth limits (default: 5 commands)
- Audio level thresholds for VAD

## Section 2: Full-Featured IDE Extensions

### Architecture

Build native extensions for VS Code, Cursor, and Claude Desktop that communicate with Barnaby via WebSocket, providing rich visual feedback directly in each editor.

### Key Components

- `ide-extensions/vscode/` - VS Code extension (TypeScript)
- `ide-extensions/cursor/` - Cursor extension (shares core code with VS Code)
- `ide-extensions/claude-desktop/` - Claude Desktop plugin
- `ide-extensions/shared/` - Shared TypeScript code for common extension logic
- `engine/barnaby_engine/ide_bridge.py` - WebSocket bridge for IDE communication
- New protocol messages for IDE-specific events

### Extension Features (Consistent Across All IDEs)

**Sidebar Panel:**
- Barnaby session status and connection state
- Recent turns with quick access to full conversation
- Quick controls for voice mode, wake word, continuous mode
- Current task/activity indicator

**Status Bar:**
- Barnaby connection status (connected/disconnected)
- Current voice mode (push-to-talk vs continuous)
- Wake word active indicator
- Quick toggle for listening state

**Inline Feedback:**
- Real-time highlighting of files being edited
- Diff previews showing changes as they happen
- Tool execution indicators in file tree
- Error/warning decorations on affected lines

**Quick Actions:**
- Right-click context menu: "Ask Barnaby about this code"
- Right-click context menu: "Explain selection"
- Right-click context menu: "Fix this file"
- Selection-based context passing to Barnaby

**Command Palette:**
- "Barnaby: Start listening"
- "Barnaby: Stop listening"
- "Barnaby: Toggle continuous mode"
- "Barnaby: Toggle wake word"
- "Barnaby: Explain selection"

**Code Lenses:**
- Annotations on code that Barnaby has touched
- Quick actions: "View changes", "Revert", "Explain"
- Timestamp indicators for when code was modified

### Claude Desktop Specifics

- Research Claude Desktop's extension/plugin API
- Architecture depends on Claude Desktop's extensibility model
- May integrate through Claude Desktop's agent system if available
- Fallback: use Claude Desktop's existing Claude Code integration if extension API is limited

### Data Flow

1. Each extension connects to Barnaby engine WebSocket on startup
2. Barnaby sends events (turn start, tool execution, file edits) to connected extensions
3. Extensions update their respective UIs (status bar, sidebar, decorations) in real-time
4. User can trigger Barnaby actions from any extension (quick actions, commands)
5. Extension sends context (active file, selection) with Barnaby requests

### Security

- Extensions connect only to localhost WebSocket
- Token-based authentication (same token as UI)
- User must explicitly enable each extension integration in Barnaby settings
- Only one extension active at a time (or all with conflict resolution)
- No data leaves local machine (same security model as main Barnaby app)

## Section 3: Workflow Automation

### Architecture

Create a scripting system for custom voice commands, macro recording, and task templates to automate common development patterns.

### Key Components

- `engine/barnaby_engine/workflows.py` - Workflow execution engine
- `engine/barnaby_engine/macro_recorder.py` - Records and replays operation sequences
- `engine/barnaby_engine/templates.py` - Pre-built task templates
- `app/src/components/WorkflowEditor.tsx` - UI for creating/editing workflows
- `app/src/components/MacroPanel.tsx` - UI for recording and managing macros
- New Barnaby commands for workflow management

### Custom Voice Commands

**Definition Format:**
```json
{
  "name": "fix_file",
  "pattern": "fix (?:the )?file {file}",
  "instruction": "Fix the issues in {file}",
  "agent": "claude"
}
```

**Features:**
- Users define command patterns with parameters
- Commands map to Barnaby instruction templates with parameter substitution
- Support regex patterns for flexible matching
- Commands can chain multiple Barnaby operations
- Parameter validation and type checking

### Macro Recording

**Recording Flow:**
1. User initiates recording via voice or UI
2. User performs sequence of voice instructions
3. System captures each turn, agent response, and file changes
4. User stops recording and names the macro
5. Macro is saved and can be replayed

**Macro Features:**
- Editable after recording (remove steps, add conditions)
- Support for variables (file names, function names) for reusability
- Conditional branching based on command success/failure
- Parameterized macros for different contexts

### Task Templates

**Pre-built Templates:**
- **TDD Cycle:** "write failing test" → "implement" → "run tests" → "refactor"
- **Debugging:** "add logging" → "reproduce" → "analyze" → "fix"
- **Code Review:** "check style" → "security scan" → "performance check"
- **Feature Implementation:** "design" → "implement" → "test" → "document"

**Template Features:**
- Templates can be customized and modified
- Templates can include conditional logic and branching
- Approval gates for critical steps
- Templates can be shared via import/export

### Data Flow

1. User creates custom command or records macro
2. Workflow engine parses and validates the definition
3. When triggered, workflow engine executes steps sequentially
4. Each step sends instructions to the appropriate agent (Claude/Cursor)
5. Results and file changes are captured for macro replay
6. Templates can include approval gates for critical steps

### Storage

- Workflows stored in `%APPDATA%\Barnaby\workflows\` as JSON files
- Built-in templates shipped with Barnaby
- User templates override built-in templates
- Import/export functionality for sharing workflows

## Section 4: Enhanced UI

### Architecture

Implement a comprehensive theming system, layout customization, and full keyboard navigation to improve user experience and accessibility.

### Key Components

- `app/src/components/ThemeProvider.tsx` - Theme management and application
- `app/src/components/LayoutManager.tsx` - Layout customization UI and state
- `app/src/components/KeyboardNav.tsx` - Keyboard navigation hooks and utilities
- `app/src/themes/` - Theme definitions (dark, light, high-contrast, user-custom)
- CSS variables for all colors, spacing, and typography
- Layout persistence in user settings

### Theme System

**Built-in Themes:**
- **Dark** (default) - Dark background, light text, optimized for long sessions
- **Light** - Light background, dark text, for bright environments
- **High Contrast** - Maximum contrast for accessibility

**Theme Features:**
- Themes define colors for all UI elements (backgrounds, text, borders, accents)
- Custom theme editor allowing users to create and save their own themes
- Theme applies to all UI components including dialogs, toasts, and panels
- System theme detection option (follow Windows light/dark mode)
- Live theme preview in settings

### Layout Customization

**Resizable Panels:**
- Users can drag panel borders to resize regions
- Minimum and maximum size constraints to prevent breakage
- Smooth resizing with visual feedback

**Collapsible Panels:**
- Sidebar, activity panel, and conversation view can be collapsed
- Collapsed state persists across sessions
- Keyboard shortcuts to toggle panels

**Preset Layouts:**
- **Default** - Balanced layout with all panels visible
- **Focus Mode** - Minimal UI, just conversation and composer
- **Debug Mode** - Expanded activity panel for detailed tool output
- **Custom** - User-saved layouts

**Layout Features:**
- Layouts can be saved and switched between
- Responsive behavior for different window sizes
- Panels auto-hide on small windows
- Layout state persists across app restarts

### Keyboard-Only Mode

**Comprehensive Navigation:**
- Every UI element accessible via keyboard
- Clear focus indicators and navigation patterns
- Logical tab order and arrow key navigation

**Customizable Shortcuts:**
- Customizable keyboard shortcuts for all actions
- Keyboard shortcut editor in Settings
- Conflict detection and resolution
- Export/import of shortcut configurations

**Command Palette:**
- Ctrl+K style command palette for quick access to any action
- Fuzzy search for commands
- Keyboard-only workflow execution
- Quick access to voice commands and workflows

**Voice + Keyboard Integration:**
- Voice commands can trigger keyboard shortcuts
- Keyboard shortcuts can trigger voice commands
- Unified action system for both input methods

### General UI Improvements

- Better visual hierarchy and spacing
- Improved icons and visual feedback
- Smoother animations and transitions
- Better error states and empty states
- Enhanced accessibility (ARIA labels, screen reader support)
- Improved responsive design for different screen sizes

### Configuration

- Theme selection and customization in Settings
- Layout presets and custom layouts saved in config
- Keyboard shortcut editor in Settings
- Export/import of themes and layouts for sharing

## Section 5: Data Flow and Integration

### Protocol Extensions

**New Protocol Message Types:**

**Voice Events:**
- `wake_word_detected` - Wake word activation
- `continuous_mode_changed` - Mode toggle state
- `command_chain_start` - Beginning of command chain
- `command_chain_end` - End of command chain

**IDE Events:**
- `ide_connected` - Extension connection established
- `ide_context_update` - Active file/selection change
- `ide_file_focus` - User focused specific file
- `ide_selection` - Text selection in editor

**Workflow Events:**
- `workflow_started` - Workflow execution initiated
- `workflow_step_complete` - Individual step finished
- `workflow_error` - Workflow execution failed
- `macro_recorded` - Macro recording completed

**UI Events:**
- `theme_changed` - Theme selection changed
- `layout_changed` - Layout configuration updated
- `keyboard_shortcut_updated` - Shortcut modified

### Cross-System Interactions

**Wake Word + Workflows:**
- Wake word detection can trigger workflow execution
- Workflows can include wake word activation/deactivation

**IDE + Workflows:**
- IDE context automatically included in workflow templates
- Workflows can manipulate IDE state (open files, change selections)

**Workflows + UI:**
- Workflows can include UI state changes (switch themes, change layouts)
- UI actions can trigger workflow execution

**Keyboard + Voice:**
- Keyboard shortcuts can trigger voice commands
- Voice commands can trigger keyboard shortcuts
- Unified action system for both input methods

### Configuration Coordination

**Unified Settings System:**
- All system configurations live in a single settings structure
- Settings validation when multiple systems interact
- Example: wake word sensitivity must be compatible with continuous mode timeout

**Import/Export:**
- Complete configuration export including all systems
- Selective import (e.g., import workflows without changing theme)
- Configuration versioning for migration support

### Performance Considerations

**Wake Word Detector:**
- Runs in separate thread to avoid blocking main loop
- Low CPU usage, optimized for always-on operation
- Configurable polling rate for performance tuning

**IDE Communication:**
- Async and non-blocking WebSocket communication
- Message queuing for high-frequency events
- Debouncing for rapid file change events

**Workflow Execution:**
- Non-blocking execution doesn't freeze UI
- Progress indicators for long-running workflows
- Cancellation support for running workflows

**Theme Changes:**
- Efficient theme application without full re-renders
- CSS variable-based theme switching
- Smooth transitions between themes

## Section 6: Testing Strategy

### Voice Features Testing

- Unit tests for wake word detection logic
- Integration tests for continuous mode VAD
- Mock audio input for reproducible tests
- Performance tests for CPU usage

### IDE Extension Testing

- Unit tests for extension logic
- Integration tests with mock Barnaby engine
- E2E tests using Playwright for each IDE
- WebSocket communication tests

### Workflow Testing

- Unit tests for workflow parser
- Integration tests for workflow execution
- Macro recording and replay tests
- Template validation tests

### UI Testing

- Component tests for theme switching
- Layout state persistence tests
- Keyboard navigation tests
- Accessibility tests (screen reader compatibility)

### Integration Testing

- Cross-system interaction tests
- Protocol message validation
- Configuration import/export tests
- Performance regression tests

## Section 7: Implementation Phases

### Phase 1: Foundation (4-6 weeks)
- Wake word detection implementation
- Continuous dictation mode
- Basic IDE bridge infrastructure
- Workflow engine core

### Phase 2: IDE Extensions (6-8 weeks)
- VS Code extension full implementation
- Cursor extension (sharing VS Code code)
- Claude Desktop integration (research + implementation)
- Rich UI feedback in all extensions

### Phase 3: Workflow Automation (4-6 weeks)
- Custom voice commands system
- Macro recording and replay
- Task templates implementation
- Workflow editor UI

### Phase 4: Enhanced UI (4-6 weeks)
- Theme system implementation
- Layout customization
- Keyboard navigation
- General UI polish

### Phase 5: Integration and Polish (4-6 weeks)
- Cross-system integration
- Performance optimization
- Testing and bug fixes
- Documentation

**Total estimated timeline:** 22-32 weeks (5-8 months)

## Section 8: Success Criteria

### Voice Features
- Wake word detection with <100ms latency
- Continuous dictation with <95% accuracy
- Command chaining supporting 5+ sequential commands

### IDE Extensions
- Extensions available for VS Code, Cursor, and Claude Desktop
- Real-time file edit highlighting with <500ms latency
- All quick actions and commands functional

### Workflow Automation
- Support for 20+ custom voice commands
- Macro recording and replay working reliably
- 10+ pre-built task templates included

### Enhanced UI
- 3 built-in themes (dark, light, high-contrast)
- 3 preset layouts + custom layout support
- Full keyboard navigation for all UI elements

### Overall
- No performance regression in existing features
- All new features fully tested
- Documentation for all new systems
- User onboarding guide for new features
