# Frontend иерархии папок: локальная проверка

## Windows PowerShell

Из корня репозитория (только forward-only миграция):

```powershell
docker compose build backend frontend
docker compose up -d postgres
docker compose run --rm backend alembic upgrade head
$env:TEST_DATABASE_URL = "postgresql+asyncpg://postgres:postgres@postgres:5432/ai_school_test"
docker compose run --rm -e TEST_DATABASE_URL=$env:TEST_DATABASE_URL backend pytest -q -rs
# В результате не должно быть skipped integration tests.
docker compose run --rm frontend npm test -- --run
docker compose run --rm frontend npm run build
docker compose up -d backend frontend
docker compose logs -f backend frontend
Start-Process "http://localhost:5173/content-bank"
Start-Process "http://localhost:8000/docs"
```

UI: `http://localhost:5173/content-bank`; API: `http://localhost:8000/api/content-bank`; OpenAPI: `http://localhost:8000/docs`.

## Ручной checklist исправленных взаимодействий

1. В корне предмета нажать «Новая папка»: открывается именованный диалог, указано расположение; после submit папка видна в корне.
2. В открытой папке создать nested folder: request относится к текущей папке, location не меняется.
3. Создать sibling с конфликтующим именем: ошибка остаётся в диалоге, введённое имя сохраняется.
4. Переименовать папку: поле предзаполнено; после успеха обновляются список и breadcrumb.
5. Открыть picker перемещения: видны root, имена, отступы и полные пути.
6. Проверить picker перемещаемой папки: самой папки и descendants среди targets нет.
7. Выбрать target по названию и переместить: UUID нигде не вводится; открытая папка остаётся на URL с тем же ID и новым breadcrumb.
8. У задания открыть picker и выбрать другую папку: задание исчезает с текущего direct level.
9. Снова открыть задание в target и выбрать root: задание появляется в корне предмета.
10. Удалить пустую папку через диалог: после подтверждения она исчезает.
11. Удалить непустую: диалог остаётся, показано понятное `folder_not_empty`, данные не теряются.
12. Проверить reload, Back и Forward: URL, subject, folder и breadcrumb восстанавливаются.
13. Выполнить действия Tab/Shift+Tab/Enter/Escape: focus удерживается в диалоге и возвращается opener.
14. На ширине 320–520 px: диалог прокручивается внутри viewport, actions не вызывают горизонтальную прокрутку.
15. Проверить отсутствие browser prompt/confirm в folder CRUD и task move.
16. Убедиться, что ни одно поле не просит UUID: picker показывает только понятные имена и пути.

Для очистки удалять только созданные тестовые задания, затем созданные папки снизу вверх. Запрещены downgrade рабочей dev-БД, `docker compose down -v`, удаление volumes и полная очистка пользовательских данных.

## Сохранённые границы

CSV/XLSX остаётся root-only. Нет drag-and-drop, recursive delete и импорта folder path. Панель фильтров, сортировка, вторая кнопка импорта и действие «Открыть» не перерабатывались.
