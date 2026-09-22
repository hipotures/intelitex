import js from '@eslint/js'
import parser from '@babel/eslint-parser'
import hooks from 'eslint-plugin-react-hooks'
import globals from 'globals'
export default [
  { ignores: ['dist/**','node_modules/**'] },
  { files: ['src/**/*.{ts,tsx}'], languageOptions: { parser, parserOptions: { requireConfigFile:false, babelOptions:{ presets:['@babel/preset-typescript','@babel/preset-react'] } }, globals: { ...globals.browser,...globals.es2022 } }, plugins: { 'react-hooks':hooks }, rules: {
    ...js.configs.recommended.rules, 'no-undef':'off','no-unused-vars':'off', // Strict TypeScript checks both with type-aware scope.
    'react-hooks/rules-of-hooks':'error', 'react-hooks/exhaustive-deps':'error', 'no-debugger':'error',
  } },
]
