import json
import jinja2
import sys
import pygments
import pygments.lexers
from pathlib import Path

if __name__ == '__main__':
    model_str = sys.stdin.read()
    m = json.loads(model_str)
    outputs = set()
    for v in m['vertices']:
        if not any(v['contexts']):
            if (out := v['stdout']):
                outputs.add(out.strip().replace("\n", "\\n"))
    
    src = m['source'].strip() + '\n'
    if len(outputs) > 0:
        outputs = list(outputs)
        outputs.sort()

        src += '\n# Outputs:'
        for out in outputs:
            src += f'\n# {out}'

    hl = pygments.highlight(src.replace('    ', '  '),
            lexer=pygments.lexers.guess_lexer_for_filename('src.py', src),
            formatter=pygments.formatters.html.HtmlFormatter(
                style='xcode',
                linenos='inline',
                linespans=True
            ),
        )

    template_file = Path(__file__).parent / 'template.html'
    template_text = template_file.read_text()
    template = jinja2.Template(template_text)
    res = template.render(
        title='State Transitions',
        model=model_str,
        hl=hl,
    )
    print(res)
