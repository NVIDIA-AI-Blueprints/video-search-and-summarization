// SPDX-License-Identifier: MIT
const React = require('react');

const Stage = React.forwardRef((props, ref) =>
  React.createElement('div', { ...props, ref, 'data-testid': 'konva-stage' }, props.children)
);
Stage.displayName = 'Stage';

const Layer = React.forwardRef((props, ref) =>
  React.createElement('div', { ...props, ref, 'data-testid': 'konva-layer' }, props.children)
);
Layer.displayName = 'Layer';

const Image = React.forwardRef((props, ref) =>
  React.createElement('img', { ref, 'data-testid': 'konva-image' })
);
Image.displayName = 'KonvaImage';

const Rect = React.forwardRef((props, ref) =>
  React.createElement('div', { ...props, ref, 'data-testid': 'konva-rect' })
);
Rect.displayName = 'Rect';

module.exports = { Stage, Layer, Image, Rect };
