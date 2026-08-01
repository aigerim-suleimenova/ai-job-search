// Поле даты: человек печатает дд.мм.гггг, а форма отправляет то, что понимает
// сервер. Календарь остаётся системным — прозрачное поле type=date лежит поверх
// значка справа. Свой текстовый порядок нужен потому, что системное поле
// показывает дату в том порядке, какой стоит в настройках компьютера, и у двух
// человек рядом он разный.
//
// Форму отправлять умеет не всякая страница. В фильтрах результатов это удобно:
// поправил дату — список перерисовался. А в быстром поиске та же самая форма
// ЗАПУСКАЕТ ПОИСК, и отправить её мимоходом, пока человек набирает год, —
// последнее, чего он ждёт. Поэтому решает страница, а не это место: отправляем
// только там, где на форме написано data-autosubmit.
(() => {
  const toText = iso => iso ? iso.slice(8, 10) + '.' + iso.slice(5, 7) + '.' + iso.slice(0, 4) : '';
  const toIso = text => {
    const m = text.match(/^(\d{2})\.(\d{2})\.(\d{4})$/);
    if (!m) return '';
    const [, d, mo, y] = m;
    const dt = new Date(+y, +mo - 1, +d);
    const real = dt.getFullYear() === +y && dt.getMonth() === +mo - 1 && dt.getDate() === +d;
    return real ? y + '-' + mo + '-' + d : '';
  };
  const maybeSubmit = el => {
    const form = el.form;
    if (form && form.hasAttribute('data-autosubmit')) form.submit();
  };

  document.querySelectorAll('.date-field').forEach(field => {
    const text = field.querySelector('.date-text');
    const native = field.querySelector('.date-native');
    if (!text || !native) return;
    text.value = toText(native.value);
    text.addEventListener('input', () => {
      const digits = text.value.replace(/\D/g, '').slice(0, 8);
      let out = digits.slice(0, 2);
      if (digits.length > 2) out += '.' + digits.slice(2, 4);
      if (digits.length > 4) out += '.' + digits.slice(4, 8);
      text.value = out;
      const iso = toIso(out);
      // трогаем форму только когда дата дописана целиком или стёрта совсем
      if (iso) { native.value = iso; maybeSubmit(text); }
      else if (!digits.length && native.value) { native.value = ''; maybeSubmit(text); }
    });
    native.addEventListener('change', () => {
      text.value = toText(native.value);
      maybeSubmit(native);
    });
  });

  // «Очистить даты» — обе сразу: стирать их по одной руками неудобно, а пустые
  // поля и значат «без ограничения».
  document.querySelectorAll('[data-clear-dates]').forEach(button => {
    button.addEventListener('click', () => {
      const scope = button.closest('form') || document;
      scope.querySelectorAll('.date-field').forEach(field => {
        const text = field.querySelector('.date-text');
        const native = field.querySelector('.date-native');
        if (text) text.value = '';
        if (native) native.value = '';
      });
    });
  });
})();
