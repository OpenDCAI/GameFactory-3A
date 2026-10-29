import { groups, measure, study } from './evaluate.js';
const reference = Object.fromEntries(Object.entries(groups.reference).map(([name, factory]) => [name,
  [30, 60, 120].flatMap(fps => [false, true].map(reducedMotion => measure(factory, { fps, reducedMotion }).report))]));
console.log(JSON.stringify({ reference, generated: study() }, null, 2));
