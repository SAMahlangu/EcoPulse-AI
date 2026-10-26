const { readFileSync } = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const { test } = require('node:test');
const ts = require('typescript');
const filename = path.join(__dirname, '../src/services/waterInvestigation.ts');
const compiled = ts.transpileModule(readFileSync(filename, 'utf8'), {compilerOptions: {module: ts.ModuleKind.CommonJS}});
const compiledModule = {exports: {}};
vm.runInNewContext(compiled.outputText, {module: compiledModule, exports: compiledModule.exports});
const {waterInvestigationPrefill} = compiledModule.exports;

test('a recorded water day makes an editable, qualified incident draft', () => {
  const result = waterInvestigationPrefill({waterInvestigation: {date: '2026-10-25', litres: 850, target_litres: 600, is_simulation: false}});
  assert.equal(result.title, 'Investigate possible water leak');
  assert.match(result.description, /does not confirm a leak/);
  assert.match(result.description, /850/);
});

test('simulation, malformed state and nonfinite values cannot prepare actual incident details', () => {
  for (const state of [null, {}, {waterInvestigation: {is_simulation: true}},
    {waterInvestigation: {date: '2026-10-25', litres: Infinity, target_litres: 600, is_simulation: false}},
    {waterInvestigation: {date: '<script>', litres: 850, target_litres: 600, is_simulation: false}}]) {
    assert.equal(waterInvestigationPrefill(state), null);
  }
});
