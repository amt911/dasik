from dasik.lib.state.change import Op, Change, Plan


def test_install_is_not_destructive():
    assert Change("packages", Op.INSTALL, "git").destructive is False


def test_remove_is_destructive():
    assert Change("packages", Op.REMOVE, "git").destructive is True


def test_disable_and_delete_are_destructive():
    assert Change("systemd", Op.DISABLE, "sshd.service").destructive is True
    assert Change("files", Op.DELETE, "/etc/foo").destructive is True


def test_empty_plan():
    p = Plan()
    assert p.is_empty() is True
    assert p.destructive() == []


def test_plan_collects_and_filters():
    p = Plan()
    p.add(Change("packages", Op.INSTALL, "git"))
    p.add(Change("packages", Op.REMOVE, "vim", reason="no longer declared"))
    assert p.is_empty() is False
    assert len(p.changes) == 2
    destructive = p.destructive()
    assert len(destructive) == 1
    assert destructive[0].item == "vim"


def test_change_render_has_sign_and_item():
    line = Change("packages", Op.INSTALL, "git").render()
    assert "+" in line and "git" in line and "packages" in line


def test_plan_render_empty_message():
    assert "No changes" in Plan().render()
