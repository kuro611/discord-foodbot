import os
import discord
from discord.ext import commands
from discord.ui import View, Button
from discord import app_commands
import traceback
import sys
import requests

import psycopg2
import random

# 環境変数読み込み
TOKEN = os.environ.get("DISCORD_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")

import google.generativeai as genai

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
genai.configure(api_key=GEMINI_API_KEY)

# Botの設定
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)
genre_map={}
style_map={}

# PostgreSQLからランダムで料理を取得
def get_random_food(food_type: str):
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cursor = conn.cursor()
        query = """
            SELECT name FROM foods
            WHERE type = %s OR type = '3'
            ORDER BY RANDOM()
            LIMIT 1
        """
        cursor.execute(query, (food_type,))
        result = cursor.fetchone()
        cursor.close()
        conn.close()
        return result[0] if result else "候補が見つかりませんでした。"
    except Exception as e:
        print(f"DBエラー: {e}")
        traceback.print_exc()
        return "トラブルブリブリ"


# 起動時の処理
@bot.event
async def on_ready():
    print("🔔 on_ready() が呼ばれました")
    await bot.tree.sync()
    await load_master()
    print(f"Bot起動完了: {bot.user}")
    
async def load_master():
    global genre_map, style_map 
    try:
        print("🔧 DB接続開始")
        conn = psycopg2.connect(DATABASE_URL)
        cursor = conn.cursor()

        cursor.execute("SELECT code, name FROM genres")
        genre_map = {code: name for code, name in cursor.fetchall()}

        cursor.execute("SELECT code, name FROM styles")
        style_map = {code: name for code, name in cursor.fetchall()}

        cursor.close()
        conn.close()
        print("✅ DBマスタ取得成功")
    except Exception as e:
        print(f"❌ DBマスタ取得失敗: {e}")

@bot.tree.command(name="genres", description="ジャンル一覧を表示します")
async def list_genres(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True, ephemeral=True)
    if not genre_map:
        await load_master()
    text = "📚 登録ジャンル一覧：\n" + "\n".join([f"{code} = {name}" for code, name in genre_map.items()])
    await interaction.followup.send(text, ephemeral=True)

@bot.tree.command(name="styles", description="スタイル一覧を表示します")
async def list_styles(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True, ephemeral=True)
    if not style_map:
        await load_master()
    text = "🎨 登録スタイル一覧：\n" + "\n".join([f"{code} = {name}" for code, name in style_map.items()])
    await interaction.followup.send(text, ephemeral=True)

@bot.tree.command(name="reload", description="ジャンル・スタイルのマスタ情報を再読み込みします")
async def reload_master(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True, ephemeral=True)
    try:
        await load_master()
        await interaction.followup.send("✅ マスタ情報を再取得しました！", ephemeral=True)
    except Exception as e:
        print(f"/reloadでのマスタ再取得失敗: {e}")
        await interaction.followup.send("❌ マスタ情報の再取得に失敗しました…", ephemeral=True)  

# ボタンクラス定義
class FoodButton(Button):
    async def callback(self, interaction: discord.Interaction):
        user_id = str(interaction.user.id)
        
        # 保険：user_statesがなければ初期化（またはエラーメッセージにしてもOK）
        state = user_states.setdefault(user_id, {})

        await interaction.response.defer(ephemeral=False)
        
        # 保険：新規ユーザーが途中から押せないように
        if len(user_states) >= 3 and user_id not in user_states:
            await interaction.followup.send("他のユーザーが操作中です。待ってね～。", ephemeral=True)
            return
        
        if self.custom_id == "buy":
            food = get_random_food("1")
            await interaction.followup.send(f"{food}！", view=FoodDetailView(food),ephemeral=False)
            if "mode" not in state or state["mode"] != "consult":
                user_states.pop(user_id, None)
        elif self.custom_id == "cook":
            food = get_random_food("2")
            recipe = get_recipe_from_rakuten(food)
            recipe_url = recipe[1] if recipe else None
            await interaction.followup.send(f"{food}！", view=RecipeView(food=food, recipe_url=recipe_url),ephemeral=False)
            if "mode" not in state or state["mode"] != "consult":
                user_states.pop(user_id, None)
        elif self.custom_id == "consult":
            await interaction.channel.send("ジャンルを選んで！", view=GenreView(),delete_after=60)
            state["mode"] = "consult"   # 継続中の状態は残す

# ビュー定義（3つのボタンを並べる）
class FoodChoiceView(View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(FoodButton(label="外食／コンビニガチャ", style=discord.ButtonStyle.primary, custom_id="buy"))
        self.add_item(FoodButton(label="作るガチャ", style=discord.ButtonStyle.success, custom_id="cook"))
        self.add_item(FoodButton(label="コンサル", style=discord.ButtonStyle.secondary, custom_id="consult"))


# メンションされたときの反応
@bot.event
async def on_message(message):
    if message.author.bot:
        return

    user_id = str(message.author.id)

    # 要望返信の処理（直前のコンサルがあれば）
    if user_id in user_states and "genre" in user_states[user_id] and "style" in user_states[user_id] and "request" not in user_states[user_id]:
        user_states[user_id]["request"] = message.content
        await message.channel.send("🤔 考え中です...",delete_after=30)
        await show_consult_result(message.channel, user_id)
        return

    # メンションされたら
    if bot.user.mentioned_in(message):
        if "responded" in user_states.get(user_id, {}):
            return  # 既に反応済み
        if "過去のおすすめ" in message.content:
            await show_user_history(message.channel, user_id)
            return
        
        if len(user_states) >= 3 and str(message.author.id) not in user_states:
            await message.channel.send("現在対応できる人数が上限に達しています。少し待ってね！",delete_after=30)
            return
        # ユーザーを仮で登録（操作開始扱い）
        user_states[str(message.author.id)] = {"mode": "start"}
        await message.channel.send("どれにする？", view = FoodChoiceView(),delete_after=60)
        return

    await bot.process_commands(message)

# ユーザー別に状態を保持する辞書（簡易的）
user_states = {}

# ジャンル選択用ビュー
class GenreView(View):
    def __init__(self):
        super().__init__(timeout=60)
        for code, name in genre_map.items():
            self.add_item(GenreButton(label=name, genre_code=code))

class GenreButton(Button):
    def __init__(self, label, genre_code):
        super().__init__(label=label, style=discord.ButtonStyle.primary)
        self.genre_code = genre_code

    async def callback(self, interaction: discord.Interaction):
        user_id = str(interaction.user.id)
        user_states.setdefault(user_id, {})["genre"] = self.genre_code
        await interaction.response.send_message("さっぱり or がっつり？", view=StyleView(), ephemeral=False,delete_after=60)

# スタイル選択用ビュー
class StyleView(View):
    def __init__(self):
        super().__init__(timeout=60)
        for code, name in style_map.items():
            self.add_item(StyleButton(label=name, style_code=code))


class StyleButton(Button):
    def __init__(self, label, style_code):
        super().__init__(label=label, style=discord.ButtonStyle.success)
        self.style_code = style_code

    async def callback(self, interaction: discord.Interaction):
        user_id = str(interaction.user.id)
        if user_id not in user_states:
            await interaction.response.send_message("先にジャンルを選んでください！", ephemeral=False,delete_after=60)
            return

        user_states[user_id]["style"] = self.style_code
        
         # レスポンス予約（これをやらないと後でエラーになる）
        await interaction.response.defer(ephemeral=False)

        # 要望入力
        await interaction.channel.send(
            "要望があればこのメッセージに返信して!",
            view=RequestView(interaction.message.id),
            delete_after=30
        )
        

class RequestView(View):
    def __init__(self, original_message_id):
        super().__init__(timeout=60)
        self.add_item(RequestNoneButton(original_message_id))

class RequestNoneButton(Button):
    def __init__(self, original_message_id):
        super().__init__(label="なし", style=discord.ButtonStyle.secondary)
        self.original_message_id = original_message_id

    async def callback(self, interaction: discord.Interaction):
        user_id = str(interaction.user.id)
        user_states[user_id]["request"] = None
        # 応答予約（これをしないと followup.send が失敗する）
        await interaction.response.defer(ephemeral=False)

        # 結果を送信
        await show_consult_result(interaction, user_id)
        
async def show_consult_result(target, user_id):
    state = user_states.get(user_id)
    genre = state["genre"]
    style = state["style"]
    request = state.get("request")

    if request is None or request.strip().lower() == "なし":
        # DBからジャンル＆スタイル一致の料理を提示
        try:
            conn = psycopg2.connect(DATABASE_URL)
            cursor = conn.cursor()
            query = """
                SELECT name FROM foods
                WHERE genre = %s AND style = %s
                ORDER BY RANDOM()
                LIMIT 1
            """
            cursor.execute(query, (genre, style))
            result = cursor.fetchone()
            cursor.close()
            conn.close()
            response = f"ほな「{result[0]}」かも！" if result else "条件に合う料理が見つかりませんでした。"
            result_food=result[0]
            successflg=True
        except Exception as e:
            print(f"DBエラー: {e}")
            response = "トラブルブリブリ"
            successflg=False
    else:
        # Gemini API呼び出し
        suggestion = get_gemini_suggestion(genre, style, request)
        if suggestion:
            response = f"ほな{suggestion}でどうや！"
            insert_food_if_new(suggestion, genre, style)
            result_food=suggestion
            successflg=True
        else:
            # フォールバックでDB検索
            try:
                conn = psycopg2.connect(DATABASE_URL)
                cursor = conn.cursor()
                query = """
                    SELECT name FROM foods
                    WHERE genre = %s AND style = %s
                    ORDER BY RANDOM()
                    LIMIT 1
                """
                cursor.execute(query, (genre, style))
                result = cursor.fetchone()
                cursor.close()
                conn.close()
                response = f"「{result[0]}」はいかがでしょう？" if result else "条件に合う料理が見つかりませんでした。"   
                result_food=result[0]
                successflg=True
            except Exception as e:
                print(f"[DB検索も失敗] {e}")
                response = "トラブルブリブリ" 
                successflg=False

    # 提案履歴をDBに保存
    if successflg:
        try:
            conn = psycopg2.connect(DATABASE_URL)
            cursor = conn.cursor()
            cursor.execute("""
    INSERT INTO consult_history (user_id, genre, style, request_text, result_text, result_food)
    VALUES (%s, %s, %s, %s, %s, %s)
""", (
    user_id, genre, style, request, response, result_food
))
            conn.commit()
            cursor.close()
            conn.close()
        except Exception as e:
            print(f"履歴保存失敗: {e}")

    if isinstance(target, discord.Interaction):
        await target.followup.send(response,view=FoodDetailView(result_food))
    else:
        await target.send(response,view=FoodDetailView(result_food))

    # 終わったらユーザー状態クリア
    del user_states[user_id]

def get_gemini_suggestion(genre_code, style_code, request_text):
    genre = genre_map.get(genre_code, genre_code)
    style = style_map.get(style_code, style_code)

    prompt = f"""ユーザーが「{genre}」を食べたい気分で、「{style}」な料理が食べたいと言っています。
また、以下の要望があります。「{request_text}」
おすすめの料理を1つ、簡潔に料理名だけ教えてください。"""

    try:
        model = genai.GenerativeModel("gemini-1.5-flash")
        response = model.generate_content(prompt)
        suggestion = response.text.strip()
        print("🟢 Gemini APIの返答：", suggestion)
        return suggestion
    except Exception as e:
        print(f"[Gemini API失敗] {e}")
        return None  # フォールバック用
    
# Geminiの回答をfoodsテーブルにも追加    
def insert_food_if_new(name, genre, style):
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cursor = conn.cursor()

        # 重複チェック（同じ名前・ジャンル・スタイルがすでにあるか）
        cursor.execute("""
            SELECT 1 FROM foods WHERE name = %s AND genre = %s AND style = %s
        """, (name, genre, style))
        exists = cursor.fetchone()

        if not exists:
            cursor.execute("""
                INSERT INTO foods (name, type, genre, style) VALUES (%s, '3', %s, %s)
            """, (name, genre, style))
            conn.commit()

        cursor.close()
        conn.close()
        print(f"✅ foodsに料理「{name}」を登録しました")
    except Exception as e:
        print(f"❌ foodsへの登録失敗: {e}")


# おすすめ表示    
async def show_user_history(channel, user_id):

    try:
        conn = psycopg2.connect(DATABASE_URL)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT result_food, genre, style, COUNT(*) AS freq
            FROM consult_history
            WHERE user_id = %s AND result_food IS NOT NULL
            GROUP BY result_food, genre, style
            ORDER BY freq DESC
            LIMIT 3
        """, (user_id,))
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
    except Exception as e:
        print(f"履歴取得失敗: {e}")
        await channel.send("履歴を取得できませんでした。",delete_after=30)
        return

    if not rows:
        await channel.send("履歴が見つかりませんでした。",delete_after=30)
        return

    marks = ["!!!", "!!", "!"]
    lines = []
    for i, (food, genre, style, _) in enumerate(rows):
        genre_name = genre_map.get(genre, genre)
        style_name = style_map.get(style, style)
        lines.append(f"{i+1}位： {food}{marks[i]} {{{genre_name}（{style_name}）}}")

    await channel.send("\n".join(lines))

class RecipeView(View):
    def __init__(self, food=None,recipe_url=None):
        super().__init__(timeout=60)
        self.food = food  # ← 安全のため保持
        self.recipe_url = recipe_url

        if recipe_url:
            self.add_item(Button(label="レシピ知りたい！", style=discord.ButtonStyle.link, url=recipe_url))
        elif food:
            self.add_item(RecipeButton(food))    

class RecipeButton(Button):
    def __init__(self,food):
        super().__init__(label="レシピ知りたい！", style=discord.ButtonStyle.secondary)
        self.food = food

    async def callback(self, interaction: discord.Interaction):
        print(f"🍳 レシピ検索対象: {self.food}")  # ← これでデバッグログ出そう
        if not self.food:
            await interaction.response.send_message("料理名が見つかりませんでした！", ephemeral=True)
            return

        # 楽天API呼び出し
        recipe = get_recipe_from_rakuten(self.food)
        if recipe:
            title, url = recipe
            await interaction.response.send_message(f"✅ {title}\n{url}", ephemeral=False)
        else:
            # Geminiで補完
            fallback = get_gemini_recipe(self.food)
            if fallback:
                await interaction.response.send_message(f"{self.food}のつくりかた！：{fallback}", ephemeral=False)
            else:
                await interaction.response.send_message("🥲 該当レシピが見つかりませんでした。がんばって作ろう！", ephemeral=False)


def get_recipe_from_rakuten(food_name):
    app_id = os.environ.get("RAKUTEN_APP_ID")
    url = f"https://app.rakuten.co.jp/services/api/Recipe/RecipeKeywordSearch/20170426"
    params = {
        "format": "json",
        "applicationId": app_id,
        "keyword": food_name
    }

    try:
        response = requests.get(url, params=params)
        data = response.json()
        if data.get("result"):
            recipe = data["result"][0]
            return recipe["recipeTitle"], recipe["recipeUrl"]
    except Exception as e:
        print(f"楽天APIエラー: {e}")
    return None

def get_gemini_recipe(food_name):
    try:
        prompt = f"{food_name} のレシピを簡単に教えてください。材料と手順を2文以内で説明してください。"
        model = genai.GenerativeModel("gemini-1.5-flash")
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        print(f"[Gemini代替失敗] {e}")
        return None

class FoodDetailView(View):
    def __init__(self, food_name):
        super().__init__(timeout=60)
        self.add_item(FoodDetailButton(food_name))

class FoodDetailButton(Button):
    def __init__(self, food_name):
        super().__init__(label="それなに～？", style=discord.ButtonStyle.secondary)
        self.food_name = food_name

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        explanation = get_food_description(self.food_name)
        await interaction.channel.send(explanation or "ごめん、うまく説明できんかった🥲",delete_after=60)

def get_food_description(food_name):
    prompt = f"「{food_name}」ってどんな料理か、簡単に説明してください。"
    try:
        model = genai.GenerativeModel("gemini-1.5-flash")
        response = model.generate_content(prompt)
        return f"{food_name}：{response.text.strip()}"
    except Exception as e:
        print(f"[Gemini料理説明失敗] {e}")
        return None

# Bot起動
def run_bot():
    bot.run(TOKEN)
