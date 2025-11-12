class GastroRouter:
    route_app_labels = {"gastronet"}

    def db_for_read(self, model, **hints):
        if model._meta.app_label in self.route_app_labels:
            return "gastronet"
        return None

    def db_for_write(self, model, **hints):
        if model._meta.app_label in self.route_app_labels:
            return "gastronet"
        return None

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        if app_label in self.route_app_labels:
            return db == "gastronet"
        return None
