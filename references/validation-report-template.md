# Шаблон validation report (раздел 37 ТЗ)

Путь: `output/validation/<process-id>-validation.md`

```markdown
# Validation

Процесс: <process_name>

Источник:
<source_file>

Шагов:
<N>

Lanes:
<N>

Gateways:
<N>

ERP-проверок:
<N>

Предположений:
<N assumption>

Открытых мест:
<N open_question>

Ошибок графа:
<N от validate_model.py>

Draw.io technical validation:
PASS | FAIL — <краткая причина>

Post-render review:
PASS | FAIL | NOT_RECORDED

Critical OPEN/ASSUMPTION blockers:
<список или none>

Общее состояние:
PASS | NEEDS_REVIEW | FAIL
```

Если есть конфликт протокол/ERP (см. `erp-conflict-rule.md`), добавить блок:

```markdown
## Конфликт протокола и типовой ERP-логики

Зафиксированный процесс отличается от найденного типового механизма ERP 2.5.
Требуется отдельная функциональная проверка.

Шаг: <id>
```
