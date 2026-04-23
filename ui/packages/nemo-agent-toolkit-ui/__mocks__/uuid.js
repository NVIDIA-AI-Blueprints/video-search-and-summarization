// Jest mock for uuid so the ESM package is not loaded (avoids "Unexpected token 'export'").
// Deterministic value for reproducible tests.
const v4 = () => '00000000-0000-4000-8000-000000000000';

module.exports = { v4 };
