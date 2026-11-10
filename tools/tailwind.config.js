// CSS 다시 만들기: npx tailwindcss@3.4.17 -c tools/tailwind.config.js -i tools/input.css -o app/static/app.css --minify
module.exports = {
  content: ['./app/templates/**/*.html', './app/filters.py'],
  theme: { extend: { fontFamily: { sans: ['"Pretendard Variable"', 'Pretendard', '"Malgun Gothic"', 'system-ui', 'sans-serif'] } } },
};
