/*
* SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
* SPDX-License-Identifier: Apache-2.0
*
* Licensed under the Apache License, Version 2.0 (the "License");
* you may not use this file except in compliance with the License.
* You may obtain a copy of the License at
*
* http://www.apache.org/licenses/LICENSE-2.0
*
* Unless required by applicable law or agreed to in writing, software
* distributed under the License is distributed on an "AS IS" BASIS,
* WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
* See the License for the specific language governing permissions and
* limitations under the License.
*/

/**
 * Monaco-based JSON editor: highlighting, line numbers, brackets, format, parse markers.
 */

import React, { useCallback, useEffect, useRef } from 'react';
import Editor, { type OnMount } from '@monaco-editor/react';
import { Box, Button, Stack } from '@mui/material';
import DataObjectIcon from '@mui/icons-material/DataObject';

export interface JsonParseLocation {
  line: number;
  column: number;
  message: string;
}

export function locateJsonParseError(text: string, err: unknown): JsonParseLocation {
  const message = err instanceof Error ? err.message : String(err);
  const posMatch = /position\s+(\d+)/i.exec(message);
  if (posMatch) {
    const pos = Math.min(Number(posMatch[1]), Math.max(0, text.length));
    const before = text.slice(0, pos);
    const lines = before.split('\n');
    return {
      line: lines.length,
      column: (lines[lines.length - 1]?.length ?? 0) + 1,
      message,
    };
  }
  const lineMatch = /line\s+(\d+)\s+column\s+(\d+)/i.exec(message);
  if (lineMatch) {
    return {
      line: Number(lineMatch[1]),
      column: Number(lineMatch[2]),
      message,
    };
  }
  return { line: 1, column: 1, message };
}

export function tryParseJson(
  text: string
): { ok: true; value: unknown } | { ok: false; error: JsonParseLocation } {
  try {
    return { ok: true, value: JSON.parse(text) };
  } catch (e: unknown) {
    return { ok: false, error: locateJsonParseError(text, e) };
  }
}

interface JsonCodeEditorProps {
  value: string;
  onChange: (value: string) => void;
  onParseErrorChange?: (error: JsonParseLocation | null) => void;
  disabled?: boolean;
  height?: number | string;
}

type MonacoApi = Parameters<OnMount>[1];
type MonacoEditorInstance = Parameters<OnMount>[0];

const JsonCodeEditor: React.FC<JsonCodeEditorProps> = ({
  value,
  onChange,
  onParseErrorChange,
  disabled = false,
  height = 420,
}) => {
  const editorRef = useRef<MonacoEditorInstance | null>(null);
  const monacoRef = useRef<MonacoApi | null>(null);

  const applyMarkers = useCallback(
    (text: string) => {
      const monaco = monacoRef.current;
      const editor = editorRef.current;
      if (!monaco || !editor) return;

      const model = editor.getModel();
      if (!model) return;

      const result = tryParseJson(text);
      if (result.ok) {
        monaco.editor.setModelMarkers(model, 'json-parse', []);
        onParseErrorChange?.(null);
        return;
      }

      const { line, column, message } = result.error;
      monaco.editor.setModelMarkers(model, 'json-parse', [
        {
          severity: monaco.MarkerSeverity.Error,
          message,
          startLineNumber: line,
          startColumn: column,
          endLineNumber: line,
          endColumn: column + 1,
        },
      ]);
      onParseErrorChange?.(result.error);
    },
    [onParseErrorChange]
  );

  const handleMount: OnMount = (editor, monaco) => {
    editorRef.current = editor;
    monacoRef.current = monaco;
    applyMarkers(editor.getValue());
  };

  useEffect(() => {
    applyMarkers(value);
  }, [value, applyMarkers]);

  const handleFormat = () => {
    const editor = editorRef.current;
    if (!editor) return;
    const current = editor.getValue();
    const result = tryParseJson(current);
    if (!result.ok) {
      onParseErrorChange?.(result.error);
      editor.revealPositionInCenter({
        lineNumber: result.error.line,
        column: result.error.column,
      });
      return;
    }
    const formatted = JSON.stringify(result.value, null, 2);
    onChange(formatted);
    editor.setValue(formatted);
    applyMarkers(formatted);
  };

  return (
    <Box>
      <Stack direction="row" justifyContent="flex-end" sx={{ mb: 1 }}>
        <Button
          size="small"
          variant="outlined"
          startIcon={<DataObjectIcon />}
          onClick={handleFormat}
          disabled={disabled}
        >
          Format JSON
        </Button>
      </Stack>
      <Box
        sx={{
          border: 1,
          borderColor: 'divider',
          borderRadius: 1,
          overflow: 'hidden',
          opacity: disabled ? 0.6 : 1,
        }}
      >
        <Editor
          height={height}
          defaultLanguage="json"
          theme="vs-light"
          value={value}
          onChange={(v) => onChange(v ?? '')}
          onMount={handleMount}
          options={{
            readOnly: disabled,
            minimap: { enabled: false },
            fontSize: 13,
            lineNumbers: 'on',
            matchBrackets: 'always',
            autoClosingBrackets: 'always',
            autoIndent: 'full',
            formatOnPaste: true,
            scrollBeyondLastLine: false,
            wordWrap: 'on',
            tabSize: 2,
            renderValidationDecorations: 'on',
            bracketPairColorization: { enabled: true },
          }}
        />
      </Box>
    </Box>
  );
};

export default JsonCodeEditor;
