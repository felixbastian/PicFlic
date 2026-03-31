from src.agents.main_agent import MainAgent as PictoAgent
from src.db import SqliteDatabase
from src.models import (
    ExpenseAnalysis,
    ImageRecord,
    MacroBreakdown,
    NutritionAnalysis,
    RecipeAnalysis,
    RecipeCollectionResult,
    RoutingDecision,
    SQLQueryPlan,
    TextRoutingDecision,
    VocabularyWorkflowResult,
)


def _mock_nutrition_analysis(image_path: str, metadata: dict | None = None) -> NutritionAnalysis:
    if "beer" in image_path:
        return NutritionAnalysis(
            ingredients=[{"name": "beer", "amount": "500 ml", "calories": 150.0}],
            category="drink",
            calories=150.0,
            macros=MacroBreakdown(carbs=12.0, protein=1.0, fat=0.0),
            tags=["alcoholic"],
            alcohol_units=1.5,
        )

    return NutritionAnalysis(
        ingredients=[
            {"name": "bun", "amount": "1 bun", "calories": 180.0},
            {"name": "beef patty", "amount": "1 patty", "calories": 280.0},
            {"name": "cheese", "amount": "1 slice", "calories": 90.0},
            {"name": "fries", "amount": "150 g", "calories": 300.0},
        ],
        category="food",
        calories=850.0,
        macros=MacroBreakdown(carbs=80.0, protein=25.0, fat=45.0),
        tags=["fast_food"],
        alcohol_units=0.0,
    )


def _mock_expense_analysis(image_path: str, metadata: dict | None = None) -> ExpenseAnalysis:
    return ExpenseAnalysis(
        description="Groceries and toiletries",
        expense_total_amount_in_euros=43.20,
        category="Lebensmitteleinkäufe",
    )


def _mock_recipe_analysis(image_path: str, metadata: dict | None = None) -> RecipeAnalysis:
    return RecipeAnalysis(
        name="Chicken rice bowl",
        description="A simple chicken rice bowl with vegetables.",
        carb_source="rice",
        vegetarian=False,
        meat="chicken",
        frequency_rotation="bi-weekly",
    )


def test_process_image_routes_to_nutrition_and_stores_record(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "src.agents.main_agent.route_image_task",
        lambda image_path, metadata=None: RoutingDecision(task_type="nutrition"),
    )
    monkeypatch.setattr("src.agents.main_agent.analyze_nutrition_image", _mock_nutrition_analysis)
    db = SqliteDatabase(tmp_path / "records.db")
    agent = PictoAgent(db)

    result = agent.process_image("beer-pint.png")

    assert result["task_type"] == "nutrition"
    assert result["analysis"]["category"] == "drink"
    assert result["analysis"]["alcohol_units"] > 0

    records = agent.list_records()
    assert len(records) == 1
    assert records[0].image_path == "beer-pint.png"
    assert records[0].task_type == "nutrition"
    assert records[0].analysis.category == "drink"


def test_process_image_routes_to_expense_and_stores_record(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "src.agents.main_agent.route_image_task",
        lambda image_path, metadata=None: RoutingDecision(task_type="expense"),
    )
    monkeypatch.setattr("src.agents.main_agent.analyze_expense_receipt", _mock_expense_analysis)
    db = SqliteDatabase(tmp_path / "records.db")
    agent = PictoAgent(db)

    result = agent.process_image("receipt.png")

    assert result["task_type"] == "expense"
    assert result["analysis"]["expense_total_amount_in_euros"] == 43.2
    assert result["analysis"]["category"] == "Lebensmitteleinkäufe"

    records = agent.list_records()
    assert len(records) == 1
    assert records[0].task_type == "expense"
    assert records[0].analysis.description == "Groceries and toiletries"


def test_get_record_by_id(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "src.agents.main_agent.route_image_task",
        lambda image_path, metadata=None: RoutingDecision(task_type="nutrition"),
    )
    monkeypatch.setattr("src.agents.main_agent.analyze_nutrition_image", _mock_nutrition_analysis)
    db = SqliteDatabase(tmp_path / "records.db")
    agent = PictoAgent(db)

    first = agent.process_image("pizza.png")
    record_id = first["record_id"]

    record = agent.get_record(record_id)
    assert record is not None
    assert record.id == record_id
    assert record.task_type == "nutrition"
    assert record.analysis.category == "food"


def test_update_nutrition_record_replaces_existing_analysis(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "src.agents.main_agent.route_image_task",
        lambda image_path, metadata=None: RoutingDecision(task_type="nutrition"),
    )
    monkeypatch.setattr("src.agents.main_agent.analyze_nutrition_image", _mock_nutrition_analysis)
    db = SqliteDatabase(tmp_path / "records.db")
    agent = PictoAgent(db)

    first = agent.process_image("beer-pint.png")
    updated = agent.update_nutrition_record(
        first["record_id"],
        {
            "ingredients": [{"name": "beer", "amount": "330 ml", "calories": 110.0}],
            "category": "drink",
            "calories": 110.0,
            "macros": {"carbs": 9.0, "protein": 1.0, "fat": 0.0},
            "tags": ["alcoholic"],
            "alcohol_units": 1.0,
        },
    )

    assert updated.analysis.calories == 110.0
    record = agent.get_record(first["record_id"])
    assert record is not None
    assert record.analysis.calories == 110.0
    assert record.analysis.ingredients[0].amount == "330 ml"


def test_process_image_routes_to_recipe_and_stores_record(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "src.agents.main_agent.route_image_task",
        lambda image_path, metadata=None: RoutingDecision(task_type="recipe"),
    )
    monkeypatch.setattr("src.agents.main_agent.analyze_recipe_image", _mock_recipe_analysis)
    db = SqliteDatabase(tmp_path / "records.db")
    agent = PictoAgent(db)

    result = agent.process_image("recipe-card.png", metadata={"user_prompt": "add this to the recipes"})

    assert result["task_type"] == "recipe"
    assert result["analysis"]["name"] == "Chicken rice bowl"
    assert result["analysis"]["carb_source"] == "rice"

    records = agent.list_records()
    assert len(records) == 1
    assert records[0].task_type == "recipe"
    assert records[0].analysis.name == "Chicken rice bowl"


def test_image_record_omits_item_count_from_persisted_nutrition_payload():
    analysis = NutritionAnalysis(
        ingredients=[{"name": "croissant", "amount": "3 x 1 piece", "calories": 690.0}],
        category="food",
        calories=690.0,
        item_count=3,
        macros=MacroBreakdown(carbs=78.0, protein=15.0, fat=36.0),
        tags=["pastry"],
        alcohol_units=0.0,
    )

    record = ImageRecord.from_analysis("croissant.jpg", "nutrition", analysis)
    serialized = record.to_dict()

    assert "item_count" not in serialized["analysis"]

    restored = ImageRecord.from_dict(serialized)
    assert restored.analysis.item_count == 1
    assert restored.analysis.calories == 690.0


def test_process_text_routes_to_echo(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "src.agents.main_agent.route_text_workflow",
        lambda text, metadata=None: TextRoutingDecision(workflow_type="echo"),
    )
    agent = PictoAgent(SqliteDatabase(tmp_path / "records.db"))

    result = agent.process_text("hello there")

    assert result["workflow_type"] == "echo"


def test_process_text_routes_to_expense_query(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "src.agents.main_agent.route_text_workflow",
        lambda text, metadata=None: TextRoutingDecision(workflow_type="expense_query"),
    )
    monkeypatch.setattr(
        "src.agents.main_agent.build_expense_query_plan",
        lambda text, metadata=None: SQLQueryPlan(
            workflow_type="expense_query",
            explanation='I am looking for all expenses in the category "Lebensmitteleinkäufe" for January 2026.',
            sql_query=(
                "SELECT COALESCE(SUM(expense_total_amount_in_euros), 0) AS result_value, "
                "'EUR' AS result_unit, 'Lebensmitteleinkäufe' AS result_label, "
                "'January 2026' AS period_label "
                "FROM fact_expenses WHERE user_id = $1"
            ),
            response_template=(
                "You spent a total of {result_value} {result_unit} on {result_label} in {period_label}."
            ),
        ),
    )
    agent = PictoAgent(SqliteDatabase(tmp_path / "records.db"))

    result = agent.process_text("What are my total expenses on groceries in January?")

    assert result["workflow_type"] == "expense_query"
    assert "Lebensmitteleinkäufe" in result["explanation"]
    assert "fact_expenses" in result["sql_query"]


def test_process_text_routes_to_nutrition_query(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "src.agents.main_agent.route_text_workflow",
        lambda text, metadata=None: TextRoutingDecision(workflow_type="nutrition_query"),
    )
    monkeypatch.setattr(
        "src.agents.main_agent.build_nutrition_query_plan",
        lambda text, metadata=None: SQLQueryPlan(
            workflow_type="nutrition_query",
            explanation="I am looking for all tracked calories in March 2026.",
            sql_query=(
                "SELECT COALESCE(SUM(calories), 0) AS result_value, "
                "'kcal' AS result_unit, 'calories' AS result_label, "
                "'March 2026' AS period_label "
                "FROM fact_consumption WHERE user_id = $1"
            ),
            response_template=(
                "You have consumed {result_value} {result_unit} of {result_label} in {period_label}."
            ),
        ),
    )
    agent = PictoAgent(SqliteDatabase(tmp_path / "records.db"))

    result = agent.process_text("How many calories have I consumed this month?")

    assert result["workflow_type"] == "nutrition_query"
    assert "tracked calories" in result["explanation"]
    assert "fact_consumption" in result["sql_query"]


def test_process_text_routes_to_vocabulary(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "src.agents.main_agent.route_text_workflow",
        lambda text, metadata=None: TextRoutingDecision(workflow_type="vocabulary"),
    )
    monkeypatch.setattr(
        "src.agents.main_agent.build_vocabulary_response",
        lambda text, metadata=None: VocabularyWorkflowResult(
            workflow_type="vocabulary",
            assistant_reply="Bonjour means hello. It is a common French greeting.",
            store_vocabulary=True,
            french_word="bonjour",
            english_description="hello; a common French greeting used when meeting someone.",
        ),
    )
    agent = PictoAgent(SqliteDatabase(tmp_path / "records.db"))

    result = agent.process_text("bonjour", metadata={"recent_history": []})

    assert result["workflow_type"] == "vocabulary"
    assert result["store_vocabulary"] is True
    assert result["french_word"] == "bonjour"
    assert "hello" in result["assistant_reply"].lower()


def test_process_text_routes_to_recipe_collection(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "src.agents.main_agent.route_text_workflow",
        lambda text, metadata=None: TextRoutingDecision(workflow_type="recipe_collection"),
    )
    monkeypatch.setattr(
        "src.agents.main_agent.build_recipe_collection_response",
        lambda text, metadata=None: RecipeCollectionResult(
            workflow_type="recipe_collection",
            name="Lemon pasta",
            description="Pasta with lemon, butter, and parmesan.",
            carb_source="noodles",
            vegetarian=True,
            meat=None,
            frequency_rotation="monthly",
        ),
    )
    agent = PictoAgent(SqliteDatabase(tmp_path / "records.db"))

    result = agent.process_text("Add this to the recipes: lemon pasta with parmesan")

    assert result["workflow_type"] == "recipe_collection"
    assert result["name"] == "Lemon pasta"
    assert result["vegetarian"] is True
