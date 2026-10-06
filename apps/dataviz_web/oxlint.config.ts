import gdp from '@gdp-ts/core/lint/oxlint';
import { defineConfig } from 'oxlint';
import antiSlop from 'ultracite/oxlint/anti-slop';
import core from 'ultracite/oxlint/core';
import react from 'ultracite/oxlint/react';
import vitest from 'ultracite/oxlint/vitest';

const gdpRules = gdp();

// Adaptaciones del baseline ultracite a las convenciones del codebase.
// Cada entrada es una excepción documentada y reversible: re-habilitarlas es
// trabajo incremental (idealmente tras commitear el WIP), no arquitectura.
const baselineExceptions = {
  // Convención del repo: funciones const-arrow (React/Effect) y componentes
  // en PascalCase.
  'func-style': 'off',
  'react/function-component-definition': 'off',
  'unicorn/filename-case': 'off',
  // Churn puro sobre el código existente.
  'sort-keys': 'off',
  'no-nested-ternary': 'off',
  'unicorn/no-array-for-each': 'off',
  'unicorn/numeric-separators-style': 'off',
  'prefer-destructuring': 'off',
  'unicorn/no-array-sort': 'off',
  'unicorn/prefer-structured-clone': 'off',
  'unicorn/prefer-type-error': 'off',
  'no-inline-comments': 'off',
  'jsdoc/check-tag-names': 'off',
  'no-plusplus': 'off',
  // ~250 regexes sin bandera `u`: añadirla cambia semántica y exige revisión
  // caso por caso (pendiente, junto con prefer-named-capture-group).
  'require-unicode-regexp': 'off',
  'prefer-named-capture-group': 'off',
  // Autofixes inseguros: reescriben a `.at()` (debilita tipos a T|undefined)
  // y a `Set.has` sobre bindings tipados como readonly arrays.
  'unicorn/prefer-at': 'off',
  'unicorn/prefer-set-has': 'off',
  // Referencias adelantadas estructurales en el editor.
  'no-use-before-define': 'off',
  // Funciones largas existentes: refactor pendiente, no bloqueante.
  'complexity': 'off',
  'require-await': 'off',
  'typescript/no-non-null-assertion': 'off',
  // Tests: migrar ~1000 asserts a toStrictEqual/toBeTruthy y desacoplar
  // expects múltiples es churn masivo sobre WIP sin commitear.
  'vitest/prefer-strict-equal': 'off',
  'vitest/prefer-to-be-truthy': 'off',
  'vitest/prefer-to-be-falsy': 'off',
  'vitest/max-expects': 'off',
  'vitest/require-top-level-describe': 'off',
  'vitest/require-mock-type-parameters': 'off',
  // Roles ARIA deliberados (patrones de accesibilidad del repo): los tests
  // de a11y exigen roles explícitos en tablas/grid como contrato verificable.
  'jsx-a11y/prefer-tag-over-role': 'off',
  'jsx-a11y/no-redundant-roles': 'off',
};

// Cola larga del baseline: 54 reglas con <15 hits cada una sobre código WIP
// sin commitear. Se re-habilitan incrementalmente tras commitear el WIP.
// Prioridades de re-adopción (impacto real): react-hooks/exhaustive-deps,
// react/set-state-in-effect, eslint/no-script-url, typescript/no-explicit-any,
// eslint/no-shadow.
const pendingReadoption = {
  'no-shadow': 'off',
  'typescript/no-explicit-any': 'off',
  'promise/prefer-await-to-then': 'off',
  'no-unused-vars': 'off',
  'unicorn/no-array-reduce': 'off',
  'unicorn/consistent-function-scoping': 'off',
  'react/refs': 'off',
  'promise/avoid-new': 'off',
  'jsx-a11y/no-noninteractive-element-to-interactive-role': 'off',
  'react-hooks/exhaustive-deps': 'off',
  'no-control-regex': 'off',
  'no-bitwise': 'off',
  'react/no-unescaped-entities': 'off',
  'react/no-object-type-as-default-prop': 'off',
  'react/hook-use-state': 'off',
  'jsx-a11y/no-noninteractive-tabindex': 'off',
  'react/immutability': 'off',
  'import/no-named-as-default': 'off',
  'react/set-state-in-effect': 'off',
  'unicorn/import-style': 'off',
  'promise/prefer-await-to-callbacks': 'off',
  'unicorn/prefer-query-selector': 'off',
  'unicorn/no-await-expression-member': 'off',
  'jsx-a11y/no-noninteractive-element-interactions': 'off',
  'jsx-a11y/control-has-associated-label': 'off',
  'no-useless-return': 'off',
  'unicorn/prefer-response-static-json': 'off',
  'react/exhaustive-effect-dependencies': 'off',
  'import/no-cycle': 'off',
  'unicorn/no-array-reverse': 'off',
  'no-await-in-loop': 'off',
  'unicorn/prefer-code-point': 'off',
  'react/jsx-no-useless-fragment': 'off',
  'no-script-url': 'off',
  'react/memo-dependencies': 'off',
  'unicorn/prefer-spread': 'off',
  'unicorn/prefer-number-properties': 'off',
  'typescript/no-inferrable-types': 'off',
  'react/preserve-manual-memoization': 'off',
  'jsx-a11y/no-static-element-interactions': 'off',
  'jsx-a11y/click-events-have-key-events': 'off',
  'unicorn/prefer-logical-operator-over-ternary': 'off',
  'react/rule-suppression': 'off',
  'vitest/require-to-throw-message': 'off',
  'no-unreachable-loop': 'off',
  'typescript/no-dynamic-delete': 'off',
  'node/callback-return': 'off',
  'unicorn/no-object-as-default-parameter': 'off',
  'unicorn/no-lonely-if': 'off',
  'unicorn/prefer-export-from': 'off',
  'oxc/branches-sharing-code': 'off',
  'react/state-in-constructor': 'off',
  'no-unused-expressions': 'off',
  'vitest/no-conditional-expect': 'off',
} as const;

// Reglas anti-slop: activas SOLO en la frontera con el backend
// (transporte HTTP, proofs de autorización y seguridad). El resto del
// codebase las hereda desactivadas hasta migrar el WIP; la lista completa
// del preset se re-habilita progresivamente.
const antiSlopRules = Object.keys(antiSlop.rules ?? {}).map((rule) => [rule, 'error'] as const);

export default defineConfig({
  extends: [core, react, antiSlop],
  ignorePatterns: core.ignorePatterns,
  // El plugin de tests se aplica vía overrides propios (más abajo) porque las
  // overrides heredadas de `extends` tienen precedencia sobre las de la raíz.
  plugins: [...(core.plugins ?? []), 'vitest'],
  jsPlugins: [...antiSlop.jsPlugins, ...gdpRules.jsPlugins],
  overrides: [
    ...(vitest.overrides ?? []),
    { files: ['**'], rules: { ...baselineExceptions, ...pendingReadoption, ...Object.fromEntries(antiSlopRules.map(([r]) => [r, 'off'])) } },
    {
      files: ['src/runtime/**', 'src/proofs/**', 'src/security/**'],
      rules: {
        ...Object.fromEntries(antiSlopRules),
        'typescript/no-non-null-assertion': 'error',
        // Patrón prescrito por gdp-ts: prover + interface del proof comparten
        // nombre (value+type spaces) e interfaces vacías que refinan Proof.
        'eslint/no-redeclare': 'off',
        'typescript/no-empty-interface': 'off',
        'typescript/no-empty-object-type': 'off',
        // Falso positivo sobre `extends Schema.TaggedError()(...)` de Effect.
        'unicorn/throw-new-error': 'off',
      },
    },
    ...gdpRules.overrides,
  ],
});
